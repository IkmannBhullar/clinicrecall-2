"""The internal reminder job endpoint (SPEC §6.3).

    POST /internal/jobs/process-reminders
    X-Job-Token: <shared secret>

Authenticated by a shared secret rather than by a user session, because the caller is a scheduler
— cron, a systemd timer, a container orchestrator — which has no user to be.

The comparison is constant-time. A secret checked with ``==`` returns as soon as two bytes differ,
so the time it takes leaks how much of a guess was right, and an attacker can recover the token
one byte at a time.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.rate_limit import ADMIN_UTILITY_LIMIT, limiter
from app.core.tokens import verify_job_token
from app.models.user import User
from app.repositories.organizations import OrganizationRepository
from app.schemas.common import ErrorResponse
from app.schemas.reminders import JobSummaryResponse
from app.services.reminders import JobSummary, ReminderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/jobs", tags=["jobs"])


def _merge(total: JobSummary, run: JobSummary) -> JobSummary:
    """Combine per-organization results into one summary for the response."""
    total.evaluated += run.evaluated
    total.eligible += run.eligible
    total.created += run.created
    total.skipped_duplicate += run.skipped_duplicate
    total.sent += run.sent
    total.failed += run.failed
    total.delivered += run.delivered
    total.statuses_recomputed += run.statuses_recomputed
    total.errors.extend(run.errors)
    return total


@router.post(
    "/process-reminders",
    response_model=JobSummaryResponse,
    summary="Run the reminder job",
    responses={401: {"model": ErrorResponse, "description": "Missing or wrong X-Job-Token"}},
)
def process_reminders(
    x_job_token: str | None = Header(default=None, alias="X-Job-Token"),
    db: Session = Depends(get_db),
) -> JobSummaryResponse:
    """Evaluate reminder rules for every practice and send what is due.

    Safe to run repeatedly — that is the point of SPEC §6.2's idempotency guarantee. A scheduler
    that fires twice, or an operator who reruns it after a failure, produces no duplicate emails.
    """
    if not verify_job_token(x_job_token):
        logger.warning("Rejected reminder job call with a missing or incorrect X-Job-Token")
        # Deliberately says nothing about whether the token was absent or merely wrong.
        raise UnauthorizedError("A valid job token is required.")

    total = JobSummary()
    service = ReminderService(db)

    for organization in OrganizationRepository(db).list_all():
        _merge(total, service.process_reminders(organization.id))

    db.commit()
    return JobSummaryResponse(**vars(total))


@router.post(
    "/process-reminders/mine",
    response_model=JobSummaryResponse,
    summary="Run the reminder job for my practice (admin demo utility)",
    responses={
        403: {"model": ErrorResponse, "description": "Not an administrator"},
        404: {"model": ErrorResponse, "description": "Demo utilities are disabled"},
    },
)
@limiter.limit(ADMIN_UTILITY_LIMIT)
def process_reminders_for_current_organization(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> JobSummaryResponse:
    """The "Run reminder job" control in Settings (SPEC §6.3).

    Exists so the job can be triggered live during a demo, from the interface, without anyone
    reaching for a terminal and a shared secret.

    Scoped to the caller's own practice, and admin-only. It is switched off entirely in
    production, because a button that sends real email to real people on demand is a demo aid
    rather than a product feature.
    """
    if not settings.demo_utilities_enabled:
        # A 404 rather than a 403: in production this endpoint simply does not exist, and saying
        # "forbidden" would advertise that it does.
        from app.core.errors import NotFoundError

        raise NotFoundError("That endpoint is not available.")

    summary = ReminderService(db).process_reminders(user.organization_id)
    db.commit()

    logger.info("Admin %s ran the reminder job for organization %s", user.id, user.organization_id)
    return JobSummaryResponse(**vars(summary))


@router.post(
    "/reset-demo",
    summary="Restore the public demo to its documented state",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or wrong X-Job-Token"},
        404: {"model": ErrorResponse, "description": "Demo utilities are not enabled"},
    },
)
def reset_demo(
    x_job_token: str | None = Header(default=None, alias="X-Job-Token"),
) -> dict[str, float | str]:
    """Wipe and re-seed the demo data.

    This is the scheduled twin of the "Reset demo data" button in Settings. That one is admin-only
    and needs a signed-in session; this one is driven by a scheduler with a shared secret and no
    user, which is why it lives here rather than there.

    It exists because a public demo is a shared mutable object. The first visitor who marks Sarah
    Johnson as scheduled changes what every later visitor sees, and after a day of that the demo
    no longer matches the script or the screenshots. An hourly reset bounds that drift to an hour.

    Guarded twice over: a valid job token, and ``demo_utilities_enabled``. A deployment that is
    not a demo has no business exposing an endpoint that deletes every row.
    """
    if not verify_job_token(x_job_token):
        logger.warning("Rejected demo reset call with a missing or incorrect X-Job-Token")
        raise UnauthorizedError("A valid job token is required.")

    if not settings.demo_utilities_enabled:
        # 404 rather than 403: an endpoint that is switched off should not confirm it exists.
        raise NotFoundError("Not found.")

    from app.demo_reset import reset_demo_data

    # Auth accounts are left alone. They are created once when the deployment is first seeded,
    # and recreating them on every reset would mean calling the Supabase admin API twenty-four
    # times a day to arrive back where it started.
    seconds = reset_demo_data(create_auth_accounts=False)

    logger.info("Reset the public demo data in %.1fs", seconds)
    return {"status": "ok", "seconds": round(seconds, 2)}
