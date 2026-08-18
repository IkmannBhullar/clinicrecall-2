"""The activity feed (SPEC §8).

Chronological, filterable by All / Reminders / Patients / Imports, and showing **initials rather
than full names**.

That last point is the reason this router does more than serialise rows. The stored payload holds
no names at all (SPEC §9) — it carries identifiers, initials, enum values and counts. The readable
sentence a person sees is assembled here, at render time, by joining to the live patient record.

So the audit log stays free of anything identifying even when exported, while the screen still
reads like English rather than like a database table.
"""

# No `from __future__ import annotations`: see the note in app/routers/patients.py.

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import READ_LIMIT, limiter
from app.models.activity_event import ActivityEvent
from app.models.enums import ActivityEventType
from app.models.patient import Patient
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.schemas.activity import ActivityEntry, ActivityResponse

router = APIRouter(prefix="/activity", tags=["activity"])

DEFAULT_PAGE_SIZE = 30

#: How each event type is described in the feed.
#:
#: Written as sentences a receptionist would say, not as event names. "Reminder delivered" rather
#: than "REMINDER_DELIVERED", and "Patients imported" rather than "PATIENT_IMPORTED" — the feed is
#: read by people, and an enum leaking onto the screen is a small failure of care that they notice.
SUMMARIES: dict[ActivityEventType, str] = {
    ActivityEventType.PATIENT_IMPORTED: "Patients imported",
    ActivityEventType.PATIENT_CREATED: "Patient added",
    ActivityEventType.PATIENT_UPDATED: "Patient details updated",
    ActivityEventType.REMINDER_SENT: "Reminder sent",
    ActivityEventType.REMINDER_DELIVERED: "Reminder delivered",
    ActivityEventType.REMINDER_FAILED: "Reminder failed",
    ActivityEventType.APPOINTMENT_SCHEDULED: "Appointment scheduled",
    ActivityEventType.ANNUAL_VISIT_COMPLETED: "Annual visit completed",
    ActivityEventType.REMINDERS_PAUSED: "Reminders paused",
    ActivityEventType.PATIENT_OPTED_OUT: "Patient unsubscribed",
    ActivityEventType.SETTINGS_UPDATED: "Settings updated",
}


def _summarise(event: ActivityEvent, patient: Patient | None) -> str:
    """Build the readable line for one event."""
    base = SUMMARIES.get(event.type, event.type.value.replace("_", " ").capitalize())

    if event.type is ActivityEventType.PATIENT_IMPORTED:
        created = event.payload.get("created", 0)
        skipped = event.payload.get("skipped", 0)
        detail = f"{created} added"
        if skipped:
            detail += f", {skipped} skipped"
        return f"{base} — {detail}"

    if patient is not None:
        return f"{base} — {patient.initials}"

    return base


@router.get("", response_model=ActivityResponse, summary="The activity feed")
@limiter.limit(READ_LIMIT)
def list_activity(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    filter: str | None = Query(
        default=None,
        description="One of: reminders, patients, imports. Anything else returns everything.",
    ),
    page: int = Query(default=1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivityResponse:
    """Return one page of the feed, newest first."""
    repository = ActivityEventRepository(db)

    # One extra row is fetched to decide whether a "Load more" control is needed, which is
    # cheaper and simpler than a second COUNT query for a feed nobody paginates deeply.
    events = list(
        repository.list_recent(
            user.organization_id,
            filter_group=filter,
            limit=DEFAULT_PAGE_SIZE + 1,
            offset=(page - 1) * DEFAULT_PAGE_SIZE,
        )
    )
    has_more = len(events) > DEFAULT_PAGE_SIZE
    events = events[:DEFAULT_PAGE_SIZE]

    # Patients and actors are resolved in bulk rather than per row: a 30-entry feed would
    # otherwise issue up to 60 extra queries to render one screen.
    patient_ids = {event.subject_patient_id for event in events if event.subject_patient_id}
    actor_ids = {event.actor_user_id for event in events if event.actor_user_id}

    patients = (
        {p.id: p for p in db.query(Patient).filter(Patient.id.in_(patient_ids)).all()}
        if patient_ids
        else {}
    )
    actors = (
        {u.id: u for u in db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    )

    entries = []
    for event in events:
        patient = patients.get(event.subject_patient_id) if event.subject_patient_id else None
        actor = actors.get(event.actor_user_id) if event.actor_user_id else None

        entries.append(
            ActivityEntry(
                id=str(event.id),
                type=event.type,
                created_at=event.created_at,
                actor_initials=actor.initials if actor else None,
                patient_initials=patient.initials if patient else None,
                patient_public_id=patient.public_id if patient else None,
                summary=_summarise(event, patient),
                payload=event.payload,
            )
        )

    return ActivityResponse(entries=entries, has_more=has_more)
