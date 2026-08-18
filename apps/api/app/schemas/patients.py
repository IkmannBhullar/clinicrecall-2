"""Patient request and response shapes (SPEC §8)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PatientStatus, PreferredContactMethod
from app.schemas.reminders import ReminderEventResponse


class PatientSummary(BaseModel):
    """One row in the patients list.

    Carries only what the table renders. The detail view fetches the rest — a list of 50 patients
    should not ship 50 reminder timelines to draw five columns.
    """

    model_config = ConfigDict(from_attributes=True)

    public_id: str = Field(description="The only patient identifier exposed anywhere (SPEC §4.2).")
    first_name: str
    last_name: str
    email: str
    phone: str | None

    last_annual_visit_date: date
    next_annual_due_date: date
    status: PatientStatus
    scheduled_for: date | None

    reminders_enabled: bool
    opted_out: bool = Field(description="Whether the patient used an unsubscribe link.")

    last_reminder_at: datetime | None = Field(
        default=None, description="When the most recent reminder was sent, for the list column."
    )
    last_reminder_status: str | None = None


class PatientDetail(PatientSummary):
    """The patient drawer (SPEC §8).

    Note what is absent, and will stay absent: no diagnosis, no condition, no visit reason, no
    notes. SPEC §1 puts clinical documentation out of scope, and a field added "just for context"
    is how that boundary erodes.
    """

    external_id: str | None
    preferred_contact_method: PreferredContactMethod
    opted_out_at: datetime | None
    created_at: datetime

    reminders: list[ReminderEventResponse] = Field(
        default_factory=list, description="The reminder timeline, newest first."
    )


class PatientListResponse(BaseModel):
    """A page of patients, with what the pagination control needs."""

    patients: list[PatientSummary]
    total: int = Field(description="Matching patients across every page, for 'x of y'.")
    page: int
    page_size: int
    total_pages: int


class ScheduleAppointmentRequest(BaseModel):
    scheduled_for: date = Field(description="The date of the appointment.")

    @field_validator("scheduled_for")
    @classmethod
    def _must_not_be_in_the_past(cls, value: date) -> date:
        """An appointment booked for last week is a typo, not a booking.

        Checked here rather than in the service because it is a property of the request, and
        rejecting it at the edge produces a field-level error the form can attach to the input.
        """
        # Compared against UTC rather than the practice's timezone: this is a sanity check on
        # obvious nonsense, and a one-day tolerance avoids rejecting a legitimate booking made
        # late in the evening from a timezone behind UTC.
        if value < datetime.now(UTC).date() - timedelta(days=1):
            raise ValueError("An appointment cannot be scheduled in the past.")
        return value


class CompleteVisitRequest(BaseModel):
    visit_date: date | None = Field(
        default=None,
        description=(
            "When the visit happened. Defaults to today. May be backdated — staff catching up on "
            "paperwork is normal — but never set in the future."
        ),
    )


class PatientActionResponse(BaseModel):
    """What the UI needs after an action, so it can update without a refetch."""

    patient: PatientDetail
    message: str = Field(description="Confirmation to show the user, in plain language.")
