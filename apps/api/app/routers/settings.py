"""Settings, and the reminder-rule toggles (SPEC §8).

Two sensitivities worth naming.

**Changing the recall interval or the timezone re-derives every patient.** Both feed
``RecallService``, so an update is not a cosmetic preference — it moves every due date and every
status badge in the practice. The recompute happens in the same transaction as the change, so
the two can never be out of step.

**The demo utilities are fenced.** "Run reminder job" and "Reset demo data" are admin-only and
disabled entirely in production. A button that mails real patients on demand, or wipes the
database, is a demo aid rather than a product feature.
"""

# No `from __future__ import annotations`: FastAPI resolves route parameter types at import time
# and cannot rebuild them from ForwardRefs. See the note in app/routers/patients.py.

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_admin
from app.core.errors import NotFoundError
from app.core.rate_limit import ADMIN_UTILITY_LIMIT, READ_LIMIT, limiter
from app.models.enums import ActivityEventType, ReminderEventStatus, ReminderRuleKey
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.schemas.common import ErrorResponse
from app.schemas.settings import (
    AccountResponse,
    ClinicSettingsResponse,
    ClinicSettingsUpdate,
    ReminderPerformance,
    ReminderRuleResponse,
    ReminderRuleUpdate,
    SettingsPageResponse,
)
from app.services.recall import RecallService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

#: Settings whose change re-derives every patient's dates and status.
RECALL_AFFECTING_FIELDS = {"annual_interval_months", "timezone"}


def _load_settings(db: Session, user: User):  # type: ignore[no-untyped-def]
    row = ClinicSettingsRepository(db).get(user.organization_id)
    if row is None:
        raise NotFoundError("Your practice's settings could not be found.")
    return row


@router.get("/settings", response_model=SettingsPageResponse, summary="Settings page")
@limiter.limit(READ_LIMIT)
def get_settings(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SettingsPageResponse:
    """Everything the Settings screen renders."""
    return SettingsPageResponse(
        clinic=ClinicSettingsResponse.model_validate(_load_settings(db, user)),
        rules=[
            ReminderRuleResponse.model_validate(rule)
            for rule in ReminderRuleRepository(db).list_ordered(user.organization_id)
        ],
        account=AccountResponse.model_validate(user),
        demo_utilities_enabled=app_settings.demo_utilities_enabled,
    )


@router.patch(
    "/settings",
    response_model=ClinicSettingsResponse,
    summary="Update clinic settings",
    responses={403: {"model": ErrorResponse, "description": "Not an administrator"}},
)
@limiter.limit(READ_LIMIT)
def update_settings(
    request: Request,
    response: Response,
    payload: ClinicSettingsUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ClinicSettingsResponse:
    """Change the practice's configuration.

    Admin-only: the recall interval and the timezone alter every patient's due date, which is not
    something a receptionist should be able to do by mistake while updating a phone number.
    """
    row = _load_settings(db, user)
    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(row, field, value)

    db.flush()

    # If anything recall-affecting changed, re-derive every patient in the same transaction. A
    # practice that switches to an 18-month cycle must not see 12-month due dates until something
    # else happens to trigger a recompute.
    if RECALL_AFFECTING_FIELDS & changes.keys():
        recall = RecallService(db)
        recall.forget_settings(user.organization_id)
        changed = recall.recompute_organization(user.organization_id)
        logger.info(
            "Settings change re-derived %d patient(s) for organization %s",
            changed,
            user.organization_id,
        )

    ActivityEventRepository(db).record(
        user.organization_id,
        event_type=ActivityEventType.SETTINGS_UPDATED,
        actor_user_id=user.id,
        # Field names only — never the values, which include the practice's contact details.
        payload={"fields": sorted(changes.keys())},
    )

    db.commit()
    return ClinicSettingsResponse.model_validate(row)


@router.get(
    "/reminders/rules", response_model=list[ReminderRuleResponse], summary="The campaign rules"
)
@limiter.limit(READ_LIMIT)
def list_rules(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReminderRuleResponse]:
    return [
        ReminderRuleResponse.model_validate(rule)
        for rule in ReminderRuleRepository(db).list_ordered(user.organization_id)
    ]


@router.patch(
    "/reminders/rules/{rule_key}",
    response_model=ReminderRuleResponse,
    summary="Turn a reminder rule on or off",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def update_rule(
    request: Request,
    response: Response,
    rule_key: ReminderRuleKey,
    payload: ReminderRuleUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReminderRuleResponse:
    """The only edit a rule allows (SPEC §8: "enable/disable toggles only").

    Not admin-restricted: turning off the 30-days-after chase is an ordinary operational
    decision, and it is immediately reversible.
    """
    rule = ReminderRuleRepository(db).get_by_key(user.organization_id, rule_key)
    if rule is None:
        raise NotFoundError("That reminder rule could not be found.")

    rule.enabled = payload.enabled
    db.commit()

    return ReminderRuleResponse.model_validate(rule)


@router.get(
    "/reminders/performance",
    response_model=ReminderPerformance,
    summary="Delivery performance",
)
@limiter.limit(READ_LIMIT)
def get_performance(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReminderPerformance:
    """The Scheduled / Sent / Delivered / Failed strip (SPEC §8)."""
    counts = ReminderEventRepository(db).count_by_status(user.organization_id)

    return ReminderPerformance(
        scheduled=counts[ReminderEventStatus.SCHEDULED] + counts[ReminderEventStatus.SENDING],
        sent=counts[ReminderEventStatus.SENT],
        delivered=counts[ReminderEventStatus.DELIVERED],
        failed=counts[ReminderEventStatus.FAILED],
        total=sum(counts.values()),
    )


@router.post(
    "/demo/reset",
    summary="Reset demo data (admin demo utility)",
    responses={
        403: {"model": ErrorResponse, "description": "Not an administrator"},
        404: {"model": ErrorResponse, "description": "Demo utilities are disabled"},
    },
)
@limiter.limit(ADMIN_UTILITY_LIMIT)
def reset_demo_data_endpoint(
    request: Request,
    response: Response,
    user: User = Depends(require_admin),
) -> dict[str, object]:
    """The "Reset demo data" control SPEC constraint D3 requires in Settings.

    Runs the same clear-and-reseed as ``make demo-reset``, so a demo can be reset from the
    interface between clinics without anyone reaching for a terminal.

    A 404 rather than a 403 when disabled: in production this endpoint simply does not exist, and
    answering "forbidden" would advertise that it does.
    """
    if not app_settings.demo_utilities_enabled:
        raise NotFoundError("That endpoint is not available.")

    # Imported here rather than at module scope: it opens its own sessions and truncates tables,
    # which is not something that should be importable as a side effect of loading a router.
    from app.demo_reset import reset_demo_data

    elapsed = reset_demo_data()
    logger.warning("Admin %s reset the demo data (%.1fs)", user.id, elapsed)

    return {
        "message": "Demo data reset to its original state.",
        "seconds": round(elapsed, 1),
    }
