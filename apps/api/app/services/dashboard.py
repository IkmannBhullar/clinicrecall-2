"""Dashboard metrics (SPEC §8).

Most of this is counting. One number is not, and it is the one worth reading carefully:
**Estimated Revenue Recovered**.

SPEC §8 anticipates the problem exactly — "Revenue formula must be defined and shown on hover, or
an office manager will poke it." They will, and they should: it is the number that decides whether
the product is worth paying for. So the definition is stated in one place, computed exactly as
stated, exposed through the API so the interface can show it on hover, and pinned by tests.

    Appointments recovered = patients who received at least one DELIVERED reminder and were then
    marked scheduled within 30 days of it.

    Estimated value = recovered x estimated_annual_visit_value

Three deliberate conservatisms, each of which makes the number smaller:

* **Delivered, not sent.** A message the provider accepted but that then bounced never reached
  anyone and cannot have caused a booking.
* **After, not merely near.** The booking must follow the reminder. A patient who booked in the
  morning and was reminded in the afternoon was not recovered by that reminder.
* **Distinct patients.** Someone reminded three times and booked once counts once.

It is labelled "Estimated" everywhere it appears, because correlation is not proof: a patient may
have booked for their own reasons on a day that happens to follow a reminder. The honest claim is
"reminded, then booked" — which is what this computes and what the tooltip says.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.activity_event import ActivityEvent
from app.models.enums import ActivityEventType, PatientStatus, ReminderEventStatus
from app.models.patient import Patient
from app.models.reminder_event import ReminderEvent
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.services.recall import RecallService

#: How long after a reminder a booking may still be credited to it (SPEC §8).
#:
#: Thirty days matches the gap between campaign rules, so a booking is attributed to the reminder
#: that plausibly caused it rather than to one from a previous cycle.
RECOVERY_WINDOW_DAYS = 30

#: The definition, in the words the interface shows on hover.
#:
#: Kept next to the query that implements it, so the two cannot drift. If the calculation changes,
#: this string is in the diff.
REVENUE_DEFINITION = (
    "Patients who received at least one delivered reminder and were then marked as scheduled "
    "within 30 days of it, multiplied by your estimated value per annual visit. "
    "Labelled an estimate because a patient may have booked for their own reasons."
)


@dataclass
class DashboardMetrics:
    """Everything the dashboard needs, in one query pass."""

    total_patients: int = 0
    due_this_month: int = 0
    overdue: int = 0
    reminders_sent_this_month: int = 0

    appointments_recovered: int = 0
    estimated_revenue_recovered: Decimal = Decimal("0.00")
    estimated_visit_value: Decimal = Decimal("0.00")
    revenue_definition: str = REVENUE_DEFINITION

    status_counts: dict[PatientStatus, int] = field(default_factory=dict)

    @property
    def needs_attention_total(self) -> int:
        """Patients someone should actually do something about today."""
        return self.status_counts.get(PatientStatus.OVERDUE, 0) + self.status_counts.get(
            PatientStatus.DUE, 0
        )


class DashboardService:
    """Computes the dashboard's numbers."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.patients = PatientRepository(session)
        self.events = ReminderEventRepository(session)
        self.settings_repo = ClinicSettingsRepository(session)
        self.recall = RecallService(session)

    def build(self, organization_id: uuid.UUID) -> DashboardMetrics:
        today = self.recall.today_for_org(organization_id)
        settings_row = self.settings_repo.get(organization_id)

        visit_value = (
            settings_row.estimated_annual_visit_value
            if settings_row is not None
            else Decimal("0.00")
        )

        metrics = DashboardMetrics(estimated_visit_value=visit_value)
        metrics.status_counts = dict(self.patients.count_by_status(organization_id))
        metrics.total_patients = sum(metrics.status_counts.values())
        metrics.overdue = metrics.status_counts.get(PatientStatus.OVERDUE, 0)
        metrics.due_this_month = self._count_due_this_month(organization_id, today)
        metrics.reminders_sent_this_month = self.events.count_sent_since(
            organization_id, self._start_of_month(today)
        )

        metrics.appointments_recovered = self.count_recovered_appointments(organization_id)
        metrics.estimated_revenue_recovered = metrics.appointments_recovered * visit_value

        return metrics

    # -----------------------------------------------------------------------------------------
    # The individual figures
    # -----------------------------------------------------------------------------------------

    def _count_due_this_month(self, organization_id: uuid.UUID, today: date) -> int:
        """Patients whose annual visit falls due in the current calendar month.

        Calendar month rather than "the next 30 days", because that is what an office manager
        means by "this month" — they are planning against a wall calendar, not a rolling window.

        Anyone already overdue is included: they were due earlier and still need seeing, and a
        figure that quietly dropped them would understate the work in front of the practice.
        """
        month_start = self._start_of_month(today).date()
        month_end = month_start + relativedelta(months=1) - timedelta(days=1)

        return len(self.patients.list_due_between(organization_id, date.min, month_end))

    @staticmethod
    def _start_of_month(today: date) -> datetime:
        return datetime(today.year, today.month, 1, tzinfo=UTC)

    def count_recovered_appointments(self, organization_id: uuid.UUID) -> int:
        """Distinct patients who were reminded and then booked (SPEC §8).

        One query rather than a loop over patients: on a practice with 40,000 records the loop
        would issue 40,000 round trips to produce a single number on a dashboard that reloads on
        every visit.

        The join reads directly as the definition — an APPOINTMENT_SCHEDULED activity event whose
        ``created_at`` falls at or after a DELIVERED reminder's ``sent_at``, and no more than 30
        days later.
        """
        reminder = ReminderEvent.__table__.alias("delivered_reminder")
        booking = ActivityEvent.__table__.alias("booking")

        statement = (
            select(func.count(func.distinct(reminder.c.patient_id)))
            .select_from(
                reminder.join(
                    booking,
                    (booking.c.subject_patient_id == reminder.c.patient_id)
                    & (booking.c.type == ActivityEventType.APPOINTMENT_SCHEDULED.value)
                    # Booked *after* the reminder. Without this, a patient who booked in the
                    # morning and was reminded that afternoon would be credited to the reminder.
                    & (booking.c.created_at >= reminder.c.sent_at)
                    & (
                        booking.c.created_at
                        <= reminder.c.sent_at + timedelta(days=RECOVERY_WINDOW_DAYS)
                    ),
                )
            )
            .where(
                reminder.c.organization_id == organization_id,
                # Delivered, not merely sent: a bounced message reached nobody.
                reminder.c.status == ReminderEventStatus.DELIVERED.value,
                reminder.c.sent_at.is_not(None),
            )
        )

        return self.session.execute(statement).scalar_one()

    # -----------------------------------------------------------------------------------------
    # Patients needing attention
    # -----------------------------------------------------------------------------------------

    def list_needing_attention(
        self, organization_id: uuid.UUID, *, limit: int = 8
    ) -> list[Patient]:
        """The dashboard's work queue: who to chase, most urgent first.

        OVERDUE before DUE, and within each, the longest-waiting first — which is the order
        someone working through the list would choose anyway.

        Deliberately excludes SCHEDULED and COMPLETED. This is a list of things to *do*, and a
        patient who has already booked is not one of them.
        """
        statement = (
            select(Patient)
            .where(
                Patient.organization_id == organization_id,
                Patient.status.in_([PatientStatus.OVERDUE, PatientStatus.DUE]),
            )
            .order_by(Patient.next_annual_due_date.asc())
            .limit(limit)
        )

        return list(self.session.execute(statement).scalars().all())
