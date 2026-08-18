"""Dashboard metrics, and the revenue calculation in particular (SPEC §8).

Most of these are counting tests. The revenue ones are not, and they are the reason this file
matters: SPEC §8 predicts that "an office manager will poke it", and a number that cannot survive
being poked is worse than no number at all.

Each conservatism in the definition gets its own test, constructed so that a naive implementation
would produce a *larger* figure. That is the direction the errors run in — every plausible
shortcut inflates the number, which is exactly the kind of mistake a customer notices.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.clinic_settings import ClinicSettings
from app.models.enums import (
    ActivityEventType,
    PatientStatus,
    ReminderChannel,
    ReminderEventStatus,
    ReminderSource,
)
from app.models.organization import Organization
from app.models.reminder_event import ReminderEvent
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.services.dashboard import RECOVERY_WINDOW_DAYS, DashboardService
from app.services.recall import RecallService
from tests.conftest import make_patient


@pytest.fixture
def dashboard(db: Session) -> DashboardService:
    return DashboardService(db)


def add_patient(db: Session, organization_id, *, days_until_due: int = -20):  # type: ignore[no-untyped-def]
    """A saved patient with correct derived fields."""
    recall = RecallService(db)
    today = recall.today_for_org(organization_id)

    patient = make_patient(organization_id)
    patient.last_annual_visit_date = today + timedelta(days=days_until_due) - timedelta(days=365)
    PatientRepository(db).create(organization_id, patient)
    recall.apply_derived_fields(organization_id, patient)
    db.flush()
    return patient


def add_reminder(
    db: Session,
    organization_id,  # type: ignore[no-untyped-def]
    patient,  # type: ignore[no-untyped-def]
    *,
    days_ago: int,
    status: ReminderEventStatus = ReminderEventStatus.DELIVERED,
) -> ReminderEvent:
    sent_at = datetime.now(UTC) - timedelta(days=days_ago)
    event = ReminderEvent(
        organization_id=organization_id,
        patient_id=patient.id,
        reminder_rule_id=None,
        source=ReminderSource.MANUAL,
        due_date_snapshot=patient.next_annual_due_date,
        channel=ReminderChannel.EMAIL,
        status=status,
        scheduled_at=sent_at,
        sent_at=sent_at,
        delivered_at=sent_at if status is ReminderEventStatus.DELIVERED else None,
    )
    db.add(event)
    db.flush()
    return event


def add_booking(db: Session, organization_id, patient, *, days_ago: int) -> None:  # type: ignore[no-untyped-def]
    """Record an APPOINTMENT_SCHEDULED event at a chosen point in the past."""
    event = ActivityEventRepository(db).record(
        organization_id,
        event_type=ActivityEventType.APPOINTMENT_SCHEDULED,
        subject_patient_id=patient.id,
        payload={"patient_initials": patient.initials},
    )
    db.flush()
    # `created_at` has a server default, so it is set after the flush.
    event.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    db.flush()


# ---------------------------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------------------------


def test_totals_count_only_this_practice(
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """A dashboard showing another practice's patients would be the worst bug in the product."""
    for _ in range(3):
        add_patient(db, organization.id)
    for _ in range(7):
        add_patient(db, other_organization.id)

    assert dashboard.build(organization.id).total_patients == 3
    assert dashboard.build(other_organization.id).total_patients == 7


def test_the_recall_overview_sums_to_the_patient_total(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """Every patient appears in exactly one band.

    If these disagreed, the bar chart and the "Total patients" card would contradict each other
    on the same screen — which is precisely the sort of thing that makes someone stop trusting
    every other number on it.
    """
    for days in (-40, -3, 10, 100, 200):
        add_patient(db, organization.id, days_until_due=days)

    metrics = dashboard.build(organization.id)

    assert sum(metrics.status_counts.values()) == metrics.total_patients == 5


def test_due_this_month_includes_patients_already_overdue(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """They were due earlier and still need seeing.

    Dropping them would understate the work in front of the practice, which is the opposite of
    what the figure is for.
    """
    add_patient(db, organization.id, days_until_due=-60)
    add_patient(db, organization.id, days_until_due=400)

    assert dashboard.build(organization.id).due_this_month == 1


# ---------------------------------------------------------------------------------------------
# The revenue calculation (SPEC §8)
# ---------------------------------------------------------------------------------------------


def test_a_reminded_then_booked_patient_counts_as_recovered(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """The happy path: reminded, then booked a week later."""
    patient = add_patient(db, organization.id)
    add_reminder(db, organization.id, patient, days_ago=14)
    add_booking(db, organization.id, patient, days_ago=7)

    assert dashboard.count_recovered_appointments(organization.id) == 1


def test_a_booking_before_the_reminder_does_not_count(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """A patient who booked on Monday was not recovered by Friday's reminder.

    A naive "has a reminder and has a booking" query would count this, inflating the number the
    customer is being asked to pay for.
    """
    patient = add_patient(db, organization.id)
    add_booking(db, organization.id, patient, days_ago=20)
    add_reminder(db, organization.id, patient, days_ago=10)

    assert dashboard.count_recovered_appointments(organization.id) == 0


def test_a_booking_outside_the_window_does_not_count(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """Booked 45 days after a reminder is a coincidence, not a recovery."""
    patient = add_patient(db, organization.id)
    add_reminder(db, organization.id, patient, days_ago=60)
    add_booking(db, organization.id, patient, days_ago=15)

    assert dashboard.count_recovered_appointments(organization.id) == 0


@pytest.mark.parametrize(
    ("gap_days", "expected"),
    [
        (0, 1),
        (1, 1),
        (RECOVERY_WINDOW_DAYS - 1, 1),
        (RECOVERY_WINDOW_DAYS, 1),
        (RECOVERY_WINDOW_DAYS + 1, 0),
        (RECOVERY_WINDOW_DAYS + 10, 0),
    ],
)
def test_the_window_boundary(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
    gap_days: int,
    expected: int,
) -> None:
    """Both edges of the 30-day recovery window.

    Both timestamps are derived from a single anchor rather than from two separate calls to
    ``datetime.now()``. An earlier version of this test used the helpers' own clocks, so "exactly
    30 days apart" was actually 30 days plus a few hundred microseconds — and the boundary case
    failed for a reason that had nothing to do with the code under test.
    """
    anchor = datetime.now(UTC) - timedelta(days=60)

    patient = add_patient(db, organization.id)
    reminder = add_reminder(db, organization.id, patient, days_ago=0)
    reminder.sent_at = anchor
    db.flush()

    add_booking(db, organization.id, patient, days_ago=0)
    booking = ActivityEventRepository(db).list_for_patient(organization.id, patient.id)[0]
    booking.created_at = anchor + timedelta(days=gap_days)
    db.flush()

    assert dashboard.count_recovered_appointments(organization.id) == expected


def test_a_failed_reminder_does_not_count(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """A message that bounced reached nobody and cannot have caused a booking.

    This is the difference between "sent" and "delivered", and it is why the query filters on
    DELIVERED rather than on `sent_at IS NOT NULL`.
    """
    patient = add_patient(db, organization.id)
    add_reminder(db, organization.id, patient, days_ago=10, status=ReminderEventStatus.FAILED)
    add_booking(db, organization.id, patient, days_ago=5)

    assert dashboard.count_recovered_appointments(organization.id) == 0


def test_a_patient_reminded_three_times_counts_once(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """Recovered *appointments*, not recovered reminders.

    Counting rows rather than distinct patients would treble this figure, and the error would
    grow with how diligently the practice used the product — the worst possible direction.
    """
    patient = add_patient(db, organization.id)
    for days in (25, 18, 11):
        add_reminder(db, organization.id, patient, days_ago=days)
    add_booking(db, organization.id, patient, days_ago=5)

    assert dashboard.count_recovered_appointments(organization.id) == 1


def test_a_booking_with_no_reminder_does_not_count(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """A patient who rang up on their own was not recovered by this product.

    Claiming them would be the single most dishonest thing the dashboard could do.
    """
    patient = add_patient(db, organization.id)
    add_booking(db, organization.id, patient, days_ago=5)

    assert dashboard.count_recovered_appointments(organization.id) == 0


def test_recovery_does_not_cross_practices(
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    theirs = add_patient(db, other_organization.id)
    add_reminder(db, other_organization.id, theirs, days_ago=10)
    add_booking(db, other_organization.id, theirs, days_ago=5)

    assert dashboard.count_recovered_appointments(organization.id) == 0
    assert dashboard.count_recovered_appointments(other_organization.id) == 1


def test_the_revenue_figure_is_recovered_times_the_configured_value(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """The arithmetic the tooltip shows, checked exactly.

    Decimal throughout, never float: an office manager checking `2 x 275 = 550` on a calculator
    must get the same answer the screen does.
    """
    clinic_settings.estimated_annual_visit_value = Decimal("275.00")
    db.flush()

    for _ in range(2):
        patient = add_patient(db, organization.id)
        add_reminder(db, organization.id, patient, days_ago=10)
        add_booking(db, organization.id, patient, days_ago=5)

    metrics = dashboard.build(organization.id)

    assert metrics.appointments_recovered == 2
    assert metrics.estimated_revenue_recovered == Decimal("550.00")
    assert isinstance(metrics.estimated_revenue_recovered, Decimal)


def test_the_definition_travels_with_the_number(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """SPEC §8 requires the formula to be shown on hover.

    Returning it with the value means the interface cannot display a stale description of a
    calculation that has since changed.
    """
    definition = dashboard.build(organization.id).revenue_definition

    assert "delivered reminder" in definition
    assert "30 days" in definition
    # It must say that it is an estimate, because correlation is not proof.
    assert "estimate" in definition.lower()


# ---------------------------------------------------------------------------------------------
# Patients needing attention
# ---------------------------------------------------------------------------------------------


def test_needs_attention_lists_the_most_overdue_first(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """The order someone working through the list would choose anyway."""
    for days in (-5, -60, -20):
        add_patient(db, organization.id, days_until_due=days)

    queue = dashboard.list_needing_attention(organization.id)
    due_dates = [patient.next_annual_due_date for patient in queue]

    assert due_dates == sorted(due_dates)


def test_needs_attention_excludes_patients_who_have_already_booked(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    dashboard: DashboardService,
) -> None:
    """This is a list of things to do. Someone who has booked is not one of them."""
    recall = RecallService(db)
    booked = add_patient(db, organization.id, days_until_due=-30)
    recall.mark_scheduled(
        organization.id, booked, recall.today_for_org(organization.id) + timedelta(days=5)
    )
    db.flush()

    assert booked.status is PatientStatus.SCHEDULED
    assert booked.id not in {p.id for p in dashboard.list_needing_attention(organization.id)}
