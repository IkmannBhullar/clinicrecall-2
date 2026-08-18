"""Reminder campaign endpoints: the test send, and the failure-recovery path (SPEC §8).

The recovery path is the interesting one. A hard bounce means the address is wrong, so "retry" on
its own would fail identically forever — the only useful action is to correct the address and
then resend. This endpoint does both in one step, which is what makes the failed-reminder queue a
place where work gets done rather than a list of things that went wrong.
"""

# No `from __future__ import annotations`: see the note in app/routers/patients.py.

import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.errors import NotFoundError
from app.core.rate_limit import SEND_REMINDER_LIMIT, limiter
from app.models.enums import ActivityEventType, ReminderEventStatus, ReminderSource
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.schemas.common import ErrorResponse
from app.schemas.reminders import ReminderEventResponse
from app.services.reminders import ReminderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reminders", tags=["reminders"])


class FailedReminder(BaseModel):
    """One entry in the recovery queue."""

    id: str
    patient_public_id: str
    patient_name: str = Field(
        description="A full name here, unlike the activity feed: this is a work queue, and "
        "someone has to know whose address to correct."
    )
    patient_email: str
    failure_reason: str | None
    failed_at: str | None


class FixEmailRequest(BaseModel):
    email: EmailStr = Field(description="The corrected address.")
    resend: bool = Field(default=True, description="Send the reminder again once corrected.")


class TestReminderRequest(BaseModel):
    patient_public_id: str | None = Field(
        default=None,
        description="Who to send to. Defaults to the most overdue patient, so the button works "
        "with no input during a demo.",
    )


def _superseded_by_a_later_success(
    repository: ReminderEventRepository,
    organization_id,
    event,  # type: ignore[no-untyped-def]
) -> bool:
    """Whether a reminder to this patient succeeded after the given failure."""
    for later in repository.list_for_patient(organization_id, event.patient_id, limit=25):
        if later.id == event.id:
            continue
        if later.status not in {ReminderEventStatus.SENT, ReminderEventStatus.DELIVERED}:
            continue
        if later.created_at and event.created_at and later.created_at > event.created_at:
            return True
    return False


@router.get("/failed", response_model=list[FailedReminder], summary="Reminders that failed")
@limiter.limit(SEND_REMINDER_LIMIT)
def list_failed(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FailedReminder]:
    """The failure-recovery queue (SPEC §8: "Failed items link to a fix-email recovery path")."""
    repository = ReminderEventRepository(db)
    events = repository.list_failed(user.organization_id)
    patients = PatientRepository(db)

    entries = []
    for event in events:
        patient = patients.get_by_id(user.organization_id, event.patient_id)
        if patient is None:
            continue

        # Skip failures that have since been superseded by a reminder that actually arrived.
        #
        # The event itself stays FAILED — that is what happened, and rewriting it would falsify
        # the patient's timeline. But this queue is a list of *outstanding work*, and a patient
        # whose address was corrected and who has since received a reminder is not outstanding.
        #
        # Without this the queue never empties: correcting an address would resend successfully
        # and leave the original failure sitting there, so staff could not tell what was still
        # broken from what they had already fixed.
        if _superseded_by_a_later_success(repository, user.organization_id, event):
            continue

        entries.append(
            FailedReminder(
                id=str(event.id),
                patient_public_id=patient.public_id,
                patient_name=patient.full_name,
                patient_email=patient.email,
                failure_reason=event.failure_reason,
                failed_at=event.sent_at.isoformat() if event.sent_at else None,
            )
        )

    return entries


@router.post(
    "/{reminder_id}/fix-email",
    summary="Correct an address and resend",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(SEND_REMINDER_LIMIT)
def fix_email_and_resend(
    request: Request,
    response: Response,
    reminder_id: str,
    payload: FixEmailRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Fix the address that bounced, and optionally send again.

    Both in one step, because they are one intention. A "retry" button on its own would resend to
    the same broken address and fail identically, which is how a recovery queue turns into a list
    nobody works through.
    """
    events = ReminderEventRepository(db)
    failed = next(
        (
            event
            for event in events.list_failed(user.organization_id, limit=200)
            if str(event.id) == reminder_id
        ),
        None,
    )
    if failed is None:
        raise NotFoundError("That failed reminder could not be found.")

    patient = PatientRepository(db).get_by_id(user.organization_id, failed.patient_id)
    if patient is None:
        raise NotFoundError("That patient could not be found.")

    previous = patient.email
    patient.email = str(payload.email).lower()

    ActivityEventRepository(db).record(
        user.organization_id,
        event_type=ActivityEventType.PATIENT_UPDATED,
        actor_user_id=user.id,
        subject_patient_id=patient.id,
        # The field that changed, never the addresses themselves (SPEC §9).
        payload={"patient_initials": patient.initials, "fields": ["email"]},
    )
    db.flush()

    resent = False
    message = f"Email address updated for {patient.first_name}."

    if payload.resend:
        service = ReminderService(db)
        try:
            service.send_manual_reminder(
                user.organization_id,
                patient.public_id,
                actor_user_id=user.id,
                source=ReminderSource.MANUAL,
            )
            resent = True
            message = f"Email address updated and a new reminder sent to {patient.first_name}."
        except Exception as exc:
            # A throttle or a second bounce. The address change still stands, which is the more
            # important half — reporting it honestly beats rolling back a correct edit.
            logger.info("Resend after email fix did not succeed: %s", exc)
            message = (
                f"Email address updated for {patient.first_name}, but the reminder could not be "
                "sent right now. Try sending it from their patient record."
            )

    db.commit()

    logger.info(
        "Email corrected for patient %s by user %s (resent=%s)",
        patient.public_id,
        user.id,
        resent,
    )
    return {
        "message": message,
        "resent": resent,
        "previous_email_changed": previous != patient.email,
    }


@router.post(
    "/test",
    response_model=ReminderEventResponse,
    summary="Send a test reminder",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
@limiter.limit(SEND_REMINDER_LIMIT)
def send_test_reminder(
    request: Request,
    response: Response,
    payload: TestReminderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReminderEventResponse:
    """ "Send Test Reminder" (SPEC §8).

    Goes through the mock provider like any other send, so what arrives is the real message
    rendered by the real template rather than a preview of it.

    Recorded with ``source = TEST`` so it is distinguishable in the timeline. Staff should be able
    to tell "we tested this" from "we chased this patient" — otherwise a demo leaves fake chases
    scattered through real history.
    """
    patients = PatientRepository(db)

    if payload.patient_public_id:
        patient = patients.get_by_public_id(user.organization_id, payload.patient_public_id)
    else:
        # Default to whoever is most overdue, so the button works with no input on stage.
        from app.models.enums import PatientStatus

        candidates = patients.list_by_status(user.organization_id, PatientStatus.OVERDUE)
        patient = candidates[0] if candidates else None

    if patient is None:
        raise NotFoundError("There is no patient to send a test reminder to.")

    event = ReminderService(db).send_manual_reminder(
        user.organization_id,
        patient.public_id,
        actor_user_id=user.id,
        source=ReminderSource.TEST,
    )
    db.commit()

    assert event.status in {ReminderEventStatus.SENT, ReminderEventStatus.FAILED}
    return ReminderEventResponse.model_validate(event)
