"""Patient endpoints (SPEC §8).

The list, the detail drawer, and the four row actions: send a reminder, mark an appointment
scheduled, mark the annual visit completed, and pause reminders.

Every route resolves its organization through ``get_current_user`` and looks patients up by
``public_id`` — never by database key (SPEC §4.2). A ``public_id`` belonging to another practice
returns 404, indistinguishable from one that does not exist, so the endpoint leaks nothing even
when probed.
"""

# NOTE: deliberately no `from __future__ import annotations` in this module.
#
# FastAPI resolves route parameter types at import time to build the request model. With
# postponed annotations every type is a string, and FastAPI cannot rebuild a parameterised
# generic such as `list[PatientStatus] | None` from a ForwardRef — the status filter below
# fails at request time with a PydanticUserError about a type that is "not fully defined".
#
# The same applies to app/routers/imports.py, for UploadFile. Python 3.12 supports `X | Y`
# natively, so the future import buys nothing in a router anyway.

import math

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.errors import ConflictError, NotFoundError
from app.core.rate_limit import READ_LIMIT, SEND_REMINDER_LIMIT, limiter
from app.models.enums import PatientStatus
from app.models.patient import Patient
from app.models.user import User
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.schemas.common import ErrorResponse
from app.schemas.patients import (
    CompleteVisitRequest,
    PatientActionResponse,
    PatientDetail,
    PatientListResponse,
    PatientSummary,
    ScheduleAppointmentRequest,
)
from app.schemas.reminders import ReminderEventResponse
from app.services.recall import RecallService
from app.services.reminders import ReminderService

router = APIRouter(prefix="/patients", tags=["patients"])

#: Rows per page. Enough to fill a desktop screen without scrolling forever, small enough that
#: the query stays fast on a practice with tens of thousands of records.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _summary(patient: Patient, events: ReminderEventRepository) -> PatientSummary:
    """Build a list row, including the "last reminder" column."""
    last = events.get_last_for_patient(patient.organization_id, patient.id)

    return PatientSummary(
        public_id=patient.public_id,
        first_name=patient.first_name,
        last_name=patient.last_name,
        email=patient.email,
        phone=patient.phone,
        last_annual_visit_date=patient.last_annual_visit_date,
        next_annual_due_date=patient.next_annual_due_date,
        status=patient.status,
        scheduled_for=patient.scheduled_for,
        reminders_enabled=patient.reminders_enabled,
        opted_out=patient.opted_out_at is not None,
        last_reminder_at=last.sent_at if last else None,
        last_reminder_status=last.status.value if last else None,
    )


def _detail(patient: Patient, events: ReminderEventRepository) -> PatientDetail:
    """Build the drawer payload, with the reminder timeline."""
    timeline = events.list_for_patient(patient.organization_id, patient.id, limit=25)
    last = timeline[0] if timeline else None

    return PatientDetail(
        public_id=patient.public_id,
        first_name=patient.first_name,
        last_name=patient.last_name,
        email=patient.email,
        phone=patient.phone,
        external_id=patient.external_id,
        preferred_contact_method=patient.preferred_contact_method,
        last_annual_visit_date=patient.last_annual_visit_date,
        next_annual_due_date=patient.next_annual_due_date,
        status=patient.status,
        scheduled_for=patient.scheduled_for,
        reminders_enabled=patient.reminders_enabled,
        opted_out=patient.opted_out_at is not None,
        opted_out_at=patient.opted_out_at,
        created_at=patient.created_at,
        last_reminder_at=last.sent_at if last else None,
        last_reminder_status=last.status.value if last else None,
        reminders=[ReminderEventResponse.model_validate(event) for event in timeline],
    )


def _require_patient(db: Session, user: User, public_id: str) -> Patient:
    """Fetch a patient in the caller's practice, or 404."""
    patient = PatientRepository(db).get_by_public_id(user.organization_id, public_id)

    if patient is None:
        raise NotFoundError("That patient could not be found.")

    return patient


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------


@router.get("", response_model=PatientListResponse, summary="List patients")
@limiter.limit(READ_LIMIT)
def list_patients(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    search: str | None = Query(default=None, description="Match on name or email."),
    status: list[PatientStatus] | None = Query(default=None, description="Status filter chips."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientListResponse:
    """Search, filter, and paginate — all server-side (SPEC §8).

    Server-side rather than fetching everything and filtering in the browser. With 55 demo
    patients either would work; with a real practice's 40,000 the browser approach means a
    multi-megabyte response before the first row appears.
    """
    repository = PatientRepository(db)
    events = ReminderEventRepository(db)

    total = repository.count_search(user.organization_id, query=search, statuses=status)
    patients = repository.search(
        user.organization_id,
        query=search,
        statuses=status,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    return PatientListResponse(
        patients=[_summary(patient, events) for patient in patients],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


@router.get(
    "/{public_id}",
    response_model=PatientDetail,
    summary="Patient detail",
    responses={404: {"model": ErrorResponse, "description": "No such patient in this practice"}},
)
@limiter.limit(READ_LIMIT)
def get_patient(
    request: Request,
    response: Response,
    public_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientDetail:
    """One patient, with their reminder timeline."""
    return _detail(_require_patient(db, user, public_id), ReminderEventRepository(db))


@router.get(
    "/{public_id}/reminders/{reminder_id}/message",
    summary="The exact email that was sent",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def get_rendered_message(
    request: Request,
    response: Response,
    public_id: str,
    reminder_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    """Return the stored rendered message for one reminder (SPEC §6.4).

    The message as it was actually sent, not a re-render from the current template. "Here is
    exactly what your patient received" is a considerably stronger claim than "here is roughly
    what we would send now", and it is the honest answer when a clinic asks.
    """
    patient = _require_patient(db, user, public_id)

    for event in ReminderEventRepository(db).list_for_patient(
        user.organization_id, patient.id, limit=100
    ):
        if str(event.id) == reminder_id:
            return {
                "subject": event.rendered_subject,
                "html": event.rendered_body_html,
                "text": event.rendered_body_text,
            }

    raise NotFoundError("That reminder could not be found.")


# ---------------------------------------------------------------------------------------------
# The four row actions (SPEC §8)
# ---------------------------------------------------------------------------------------------


@router.post(
    "/{public_id}/send-reminder",
    response_model=PatientActionResponse,
    summary="Send a reminder now",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Reminders are off, or no valid email"},
        429: {"model": ErrorResponse, "description": "Sent to this patient too recently"},
    },
)
@limiter.limit(SEND_REMINDER_LIMIT)
def send_reminder(
    request: Request,
    response: Response,
    public_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientActionResponse:
    """The live "Send Reminder" beat in the demo (SPEC §11, step 6).

    A manual send carries ``reminder_rule_id = NULL``, so it can never collide with a rule-driven
    reminder for the same patient and due date — which is what stops this raising a duplicate-key
    error on stage (SPEC §6.2). A one-hour per-patient cooldown protects the patient's inbox
    instead.
    """
    service = ReminderService(db)
    service.send_manual_reminder(user.organization_id, public_id, actor_user_id=user.id)
    db.commit()

    patient = _require_patient(db, user, public_id)

    return PatientActionResponse(
        patient=_detail(patient, ReminderEventRepository(db)),
        message=f"Reminder sent to {patient.first_name}.",
    )


@router.post(
    "/{public_id}/schedule",
    response_model=PatientActionResponse,
    summary="Mark an appointment as scheduled",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def mark_scheduled(
    request: Request,
    response: Response,
    public_id: str,
    payload: ScheduleAppointmentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientActionResponse:
    """Record that the patient has booked.

    Writes the activity event whose timestamp the "appointments recovered" figure depends on
    (SPEC §8) — `scheduled_for` is the appointment date, which is a different question from when
    someone marked it as booked.
    """
    patient = _require_patient(db, user, public_id)

    RecallService(db).mark_scheduled(
        user.organization_id, patient, payload.scheduled_for, actor_user_id=user.id
    )
    db.commit()

    return PatientActionResponse(
        patient=_detail(patient, ReminderEventRepository(db)),
        message=f"Appointment recorded for {payload.scheduled_for.strftime('%-d %B %Y')}.",
    )


@router.post(
    "/{public_id}/complete",
    response_model=PatientActionResponse,
    summary="Mark the annual visit as completed",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def mark_completed(
    request: Request,
    response: Response,
    public_id: str,
    payload: CompleteVisitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientActionResponse:
    """Close the loop: the visit happened, so the cycle rolls forward a year.

    The single most important transition in the product — it is the step that turns a recovered
    appointment into a completed one and sets up next year's recall.
    """
    patient = _require_patient(db, user, public_id)

    try:
        RecallService(db).mark_completed(
            user.organization_id, patient, visit_date=payload.visit_date, actor_user_id=user.id
        )
    except ValueError as exc:
        # Raised for a future visit date. Surfaced as a 409 with the service's own message, which
        # is already written for a person.
        raise ConflictError(str(exc)) from exc

    db.commit()

    return PatientActionResponse(
        patient=_detail(patient, ReminderEventRepository(db)),
        message=(
            f"Visit recorded. {patient.first_name} is next due "
            f"{patient.next_annual_due_date.strftime('%-d %B %Y')}."
        ),
    )


@router.post(
    "/{public_id}/pause",
    response_model=PatientActionResponse,
    summary="Pause reminders for this patient",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def pause_reminders(
    request: Request,
    response: Response,
    public_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientActionResponse:
    """Stop reminders at the practice's request."""
    patient = _require_patient(db, user, public_id)

    RecallService(db).pause_reminders(user.organization_id, patient, actor_user_id=user.id)
    db.commit()

    return PatientActionResponse(
        patient=_detail(patient, ReminderEventRepository(db)),
        message=f"Reminders paused for {patient.first_name}.",
    )


@router.post(
    "/{public_id}/resume",
    response_model=PatientActionResponse,
    summary="Resume reminders for this patient",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
@limiter.limit(READ_LIMIT)
def resume_reminders(
    request: Request,
    response: Response,
    public_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientActionResponse:
    """Undo a pause.

    Refuses if the patient opted out themselves. Staff can reverse their own pause; only the
    patient can reverse theirs, through the link in the email they were sent. If this endpoint
    cleared ``opted_out_at``, the unsubscribe link would be decorative.
    """
    patient = _require_patient(db, user, public_id)

    if patient.opted_out_at is not None:
        raise ConflictError(
            "This patient unsubscribed themselves, so reminders cannot be resumed from here. "
            "They would need to ask your practice to re-subscribe them."
        )

    RecallService(db).resume_reminders(user.organization_id, patient, actor_user_id=user.id)
    db.commit()

    return PatientActionResponse(
        patient=_detail(patient, ReminderEventRepository(db)),
        message=f"Reminders resumed for {patient.first_name}.",
    )
