"""RecallService against a real database (SPEC §5.1, §5.4).

The pure functions are covered exhaustively in ``test_recall_status.py`` and
``test_recall_dates.py``. What is tested here is the half that touches state: that the cached
``patients.status`` column is written correctly, that the recall transitions do what they claim,
and — the requirement SPEC §5.1 states outright — that stored status never drifts from what
``compute_status`` would return.

The drift guard is the most valuable test in this file. A denormalised column is a bug waiting to
happen: it is correct at the moment it is written and slowly stops being correct as the calendar
moves or as some new code path forgets to recompute. Checking every patient against a fresh
computation is the only way to catch that, and it costs almost nothing to run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.clinic_settings import ClinicSettings
from app.models.enums import ActivityEventType, PatientStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.services.recall import (
    RecallService,
    compute_status,
    recall_input_from_patient,
)
from tests.conftest import make_patient


@pytest.fixture
def recall(db: Session) -> RecallService:
    return RecallService(db)


# ---------------------------------------------------------------------------------------------
# Settings drive the calculation (SPEC §5.3: "not a constant")
# ---------------------------------------------------------------------------------------------


def test_the_due_date_uses_the_practices_own_interval(
    db: Session, organization: Organization, recall: RecallService
) -> None:
    """An 18-month recall practice must get 18-month due dates, not 12."""
    db.add(
        ClinicSettings(
            organization_id=organization.id,
            clinic_name="Eighteen Month Clinic",
            annual_interval_months=18,
        )
    )
    db.flush()

    assert recall.interval_months_for_org(organization.id) == 18
    assert recall.due_date_for(organization.id, date(2025, 3, 14)) == date(2026, 9, 14)


def test_a_practice_with_no_settings_row_still_works(
    db: Session, organization: Organization, recall: RecallService
) -> None:
    """A missing settings row must not make every due date uncomputable.

    RecallService reads settings on every status computation, so this would otherwise be a hard
    failure for a brand-new organization rather than a mildly wrong default.
    """
    assert recall.interval_months_for_org(organization.id) == 12
    assert isinstance(recall.today_for_org(organization.id), date)


def test_changing_the_interval_is_visible_immediately(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """The settings cache holds the ORM object, so an update needs no invalidation.

    SQLAlchemy's identity map guarantees the cached instance *is* the instance any other code in
    this session is holding. Changing the recall interval in Settings and immediately recomputing
    therefore uses the new value — which is the behaviour anyone would expect, and worth pinning
    down so a future "optimisation" that caches the integer instead does not quietly break it.
    """
    assert recall.interval_months_for_org(organization.id) == 12

    clinic_settings.annual_interval_months = 24
    db.flush()

    assert recall.interval_months_for_org(organization.id) == 24


def test_settings_created_after_a_lookup_are_picked_up(
    db: Session, organization: Organization, recall: RecallService
) -> None:
    """A missing settings row must not be cached as "missing forever".

    The organization-setup path and the seed both read settings and then create them. If the
    absence were cached, every patient derived afterwards in the same request would silently use
    the fallback 12-month interval instead of the practice's real one — and the resulting due
    dates would look entirely plausible while being wrong.
    """
    # No settings row yet: the fallback is used.
    assert recall.interval_months_for_org(organization.id) == 12

    db.add(
        ClinicSettings(
            organization_id=organization.id,
            clinic_name="Green Valley Family Clinic",
            annual_interval_months=18,
        )
    )
    db.flush()

    assert recall.interval_months_for_org(organization.id) == 18


# ---------------------------------------------------------------------------------------------
# apply_derived_fields is the sole writer of both derived columns
# ---------------------------------------------------------------------------------------------


def test_derived_fields_are_written_from_the_last_visit_date(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    today = recall.today_for_org(organization.id)
    patient = make_patient(organization.id)
    patient.last_annual_visit_date = today - timedelta(days=400)
    # Deliberately wrong values, to prove they are overwritten rather than trusted.
    patient.next_annual_due_date = date(2000, 1, 1)
    patient.status = PatientStatus.ACTIVE

    recall.apply_derived_fields(organization.id, patient)

    assert patient.next_annual_due_date == recall.due_date_for(
        organization.id, today - timedelta(days=400)
    )
    # 400 days since the last visit with a 12-month cycle: about 35 days overdue.
    assert patient.status is PatientStatus.OVERDUE


def test_recompute_reports_whether_the_status_moved(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    today = recall.today_for_org(organization.id)
    patient = make_patient(organization.id)
    patient.last_annual_visit_date = today - timedelta(days=400)
    patient.status = PatientStatus.ACTIVE

    assert recall.recompute(organization.id, patient) is True
    # Second run: already correct, so nothing changes.
    assert recall.recompute(organization.id, patient) is False


def test_recompute_organization_corrects_stale_statuses(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """This is what stops a demo left running overnight from lying.

    Nobody touched these records — only the calendar moved. Statuses go stale on their own, and
    this is the sweep that fixes them.
    """
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)

    for days_ago in (400, 370, 100):
        patient = make_patient(organization.id)
        patient.last_annual_visit_date = today - timedelta(days=days_ago)
        # Everyone wrongly marked ACTIVE, as though the cache had never been updated.
        patient.status = PatientStatus.ACTIVE
        repo.create(organization.id, patient)
    db.flush()

    changed = recall.recompute_organization(organization.id)

    assert changed == 2  # the 400- and 370-day patients move; the 100-day one is genuinely ACTIVE
    assert recall.recompute_organization(organization.id) == 0  # idempotent


# ---------------------------------------------------------------------------------------------
# Marking an appointment scheduled
# ---------------------------------------------------------------------------------------------


def test_marking_scheduled_moves_an_overdue_patient(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    recall: RecallService,
) -> None:
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)

    patient = make_patient(organization.id)
    patient.last_annual_visit_date = today - timedelta(days=400)
    repo.create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()
    assert patient.status is PatientStatus.OVERDUE

    recall.mark_scheduled(
        organization.id, patient, today + timedelta(days=9), actor_user_id=staff_user.id
    )

    assert patient.scheduled_for == today + timedelta(days=9)
    assert patient.status is PatientStatus.SCHEDULED


def test_marking_scheduled_records_an_activity_event(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    recall: RecallService,
) -> None:
    """The event's ``created_at`` is what the revenue calculation uses (SPEC §8).

    ``scheduled_for`` is the date of the appointment; the recovery metric needs to know when
    someone *marked it as booked*, which is a different question.
    """
    repo = PatientRepository(db)
    patient = repo.create(
        organization.id, make_patient(organization.id, first_name="Sarah", last_name="Johnson")
    )
    db.flush()

    today = recall.today_for_org(organization.id)
    recall.mark_scheduled(
        organization.id, patient, today + timedelta(days=9), actor_user_id=staff_user.id
    )
    db.flush()

    events = ActivityEventRepository(db).list_for_patient(organization.id, patient.id)

    assert len(events) == 1
    assert events[0].type is ActivityEventType.APPOINTMENT_SCHEDULED
    assert events[0].actor_user_id == staff_user.id


def test_activity_payloads_carry_initials_not_names(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    recall: RecallService,
) -> None:
    """SPEC §9 data minimisation, checked rather than trusted.

    An audit table is the one most likely to be exported or shipped to a log aggregator, so what
    goes into it matters more than it looks.
    """
    repo = PatientRepository(db)
    patient = repo.create(
        organization.id,
        make_patient(
            organization.id,
            first_name="Sarah",
            last_name="Johnson",
            email="sarah.johnson@example.com",
        ),
    )
    db.flush()

    today = recall.today_for_org(organization.id)
    recall.mark_scheduled(organization.id, patient, today + timedelta(days=9))
    recall.mark_completed(organization.id, patient, actor_user_id=staff_user.id)
    db.flush()

    for event in ActivityEventRepository(db).list_for_patient(organization.id, patient.id):
        serialised = str(event.payload)
        assert "Sarah" not in serialised
        assert "Johnson" not in serialised
        assert "@example.com" not in serialised
        assert event.payload.get("patient_initials") == "SJ"


# ---------------------------------------------------------------------------------------------
# Completing a visit — the loop closing (SPEC §5.4 "completion recompute")
# ---------------------------------------------------------------------------------------------


def test_completing_a_visit_rolls_the_cycle_forward(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    recall: RecallService,
) -> None:
    """The single most important transition in the product.

    An overdue patient is seen; their next due date moves a year out and they show as COMPLETED
    so staff get visible confirmation.
    """
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)

    patient = make_patient(organization.id)
    patient.last_annual_visit_date = today - timedelta(days=400)
    repo.create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()
    assert patient.status is PatientStatus.OVERDUE

    recall.mark_completed(organization.id, patient, actor_user_id=staff_user.id)

    assert patient.last_annual_visit_date == today
    assert patient.next_annual_due_date == recall.due_date_for(organization.id, today)
    assert patient.status is PatientStatus.COMPLETED


def test_completing_a_visit_clears_the_scheduled_date(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """The appointment was kept, so it is no longer pending.

    This matters because SCHEDULED outranks COMPLETED in the precedence order — leaving the date
    set would keep the patient showing as "Scheduled" after they had already been seen.
    """
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    recall.mark_scheduled(organization.id, patient, today + timedelta(days=3))
    assert patient.status is PatientStatus.SCHEDULED

    recall.mark_completed(organization.id, patient)

    assert patient.scheduled_for is None
    assert patient.status is PatientStatus.COMPLETED


def test_a_completed_visit_decays_to_active_after_thirty_days(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """Nothing about the patient changes — only the calendar moves."""
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    recall.mark_completed(organization.id, patient)
    assert patient.status is PatientStatus.COMPLETED

    # Ask what the status would be 31 days from now, without waiting a month.
    later = compute_status(recall_input_from_patient(patient), today + timedelta(days=31))
    assert later is PatientStatus.ACTIVE


def test_a_visit_cannot_be_completed_in_the_future(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """Backdating a visit is legitimate; forward-dating one is not.

    A future completion would push the next due date beyond a full cycle and quietly remove the
    patient from every recall list.
    """
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    with pytest.raises(ValueError, match="future"):
        recall.mark_completed(organization.id, patient, visit_date=today + timedelta(days=1))


def test_a_backdated_completion_is_accepted(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """Staff catching up on paperwork is the normal case, not an error."""
    repo = PatientRepository(db)
    today = recall.today_for_org(organization.id)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    recall.mark_completed(organization.id, patient, visit_date=today - timedelta(days=6))

    assert patient.last_annual_visit_date == today - timedelta(days=6)
    assert patient.status is PatientStatus.COMPLETED


# ---------------------------------------------------------------------------------------------
# Pausing and opting out
# ---------------------------------------------------------------------------------------------


def test_pausing_reminders_makes_a_patient_inactive(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    recall: RecallService,
) -> None:
    repo = PatientRepository(db)
    patient = repo.create(organization.id, make_patient(organization.id, days_until_due=-30))
    db.flush()

    recall.pause_reminders(organization.id, patient, actor_user_id=staff_user.id)

    assert patient.reminders_enabled is False
    assert patient.status is PatientStatus.INACTIVE


def test_resuming_reminders_does_not_undo_a_patient_opt_out(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """Staff can undo their own pause. They cannot undo the patient's decision.

    If a staff member could clear ``opted_out_at`` from the UI, the unsubscribe link would be
    decorative. Only the patient can reverse it, through the link in the email they were sent.
    """
    repo = PatientRepository(db)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    recall.record_opt_out(organization.id, patient, opted_out_at=datetime.now(UTC))
    assert patient.status is PatientStatus.INACTIVE

    recall.resume_reminders(organization.id, patient)

    assert patient.reminders_enabled is True
    assert patient.opted_out_at is not None
    assert patient.status is PatientStatus.INACTIVE


def test_opting_out_records_an_activity_event_with_no_actor(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    recall: RecallService,
) -> None:
    """The patient did this, not a staff member — so there is no actor to attribute it to."""
    repo = PatientRepository(db)
    patient = repo.create(organization.id, make_patient(organization.id))
    db.flush()

    recall.record_opt_out(organization.id, patient, opted_out_at=datetime.now(UTC))
    db.flush()

    events = ActivityEventRepository(db).list_for_patient(organization.id, patient.id)

    assert len(events) == 1
    assert events[0].type is ActivityEventType.PATIENT_OPTED_OUT
    assert events[0].actor_user_id is None


# ---------------------------------------------------------------------------------------------
# The drift guard (SPEC §5.1)
# ---------------------------------------------------------------------------------------------


def _seed_every_band(db: Session, organization_id: uuid.UUID, recall: RecallService) -> None:
    """Create one patient in each status band, written through the service.

    Uses ``apply_derived_fields`` rather than setting status by hand, because the point of the
    drift guard is to check that the *real* write path produces correct values.
    """
    repo = PatientRepository(db)
    today = recall.today_for_org(organization_id)

    # (days since last visit, extra configuration) chosen to land in each band with a 12-month
    # cycle: 365 days since the visit means due today.
    specifications: list[tuple[int, dict[str, object]]] = [
        (100, {}),  # ACTIVE
        (350, {}),  # DUE_SOON
        (365, {}),  # DUE
        (400, {}),  # OVERDUE
        (400, {"scheduled_for": today + timedelta(days=9)}),  # SCHEDULED
        (6, {}),  # COMPLETED
        (400, {"reminders_enabled": False}),  # INACTIVE (paused)
        (400, {"opted_out_at": datetime.now(UTC)}),  # INACTIVE (opted out)
    ]

    for days_ago, overrides in specifications:
        patient = make_patient(organization_id)
        patient.last_annual_visit_date = today - timedelta(days=days_ago)
        for field, value in overrides.items():
            setattr(patient, field, value)
        repo.create(organization_id, patient)
        recall.apply_derived_fields(organization_id, patient)

    db.flush()


def test_every_band_is_reachable_through_the_service(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """Before trusting the drift guard, prove the fixture actually spans the bands.

    A drift guard over a population that is all ACTIVE would pass trivially and prove nothing.
    """
    _seed_every_band(db, organization.id, recall)

    observed = {patient.status for patient in PatientRepository(db).list_all(organization.id)}

    assert observed == {
        PatientStatus.ACTIVE,
        PatientStatus.DUE_SOON,
        PatientStatus.DUE,
        PatientStatus.OVERDUE,
        PatientStatus.SCHEDULED,
        PatientStatus.COMPLETED,
        PatientStatus.INACTIVE,
    }


def test_stored_status_never_drifts_from_a_fresh_computation(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """THE DRIFT GUARD (SPEC §5.1).

    "Add a test that walks every patient in a seeded org and asserts
    ``stored_status == compute_status(...)``."

    ``patients.status`` is a denormalised cache. A cache is correct when written and stops being
    correct the moment a code path forgets to recompute — silently, and in a way that looks
    entirely plausible on screen. This is the check that catches it.

    Phase 7 runs the same assertion against the full 55-patient demo seed.
    """
    _seed_every_band(db, organization.id, recall)

    drifted = recall.find_drifted_patients(organization.id)

    assert drifted == [], "Stored status disagrees with compute_status for: " + ", ".join(
        f"{patient.public_id} stored={patient.status.value} expected={expected.value}"
        for patient, expected in drifted
    )


def test_the_drift_guard_actually_detects_drift(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """Prove the guard is not vacuous.

    A test that only ever passes is worse than no test, because it produces false confidence.
    Corrupting one row on purpose confirms the detector fires.
    """
    _seed_every_band(db, organization.id, recall)
    assert recall.find_drifted_patients(organization.id) == []

    victim = PatientRepository(db).list_all(organization.id)[0]
    correct_status = victim.status
    victim.status = (
        PatientStatus.INACTIVE
        if correct_status is not PatientStatus.INACTIVE
        else PatientStatus.ACTIVE
    )
    db.flush()

    drifted = recall.find_drifted_patients(organization.id)

    assert len(drifted) == 1
    assert drifted[0][0].id == victim.id
    assert drifted[0][1] is correct_status


def test_find_drifted_patients_does_not_repair_what_it_measures(
    db: Session, organization: Organization, clinic_settings: ClinicSettings, recall: RecallService
) -> None:
    """A diagnostic that silently fixes the thing it reports cannot be used to detect a bug."""
    _seed_every_band(db, organization.id, recall)

    victim = PatientRepository(db).list_all(organization.id)[0]
    victim.status = PatientStatus.INACTIVE
    db.flush()

    recall.find_drifted_patients(organization.id)

    assert victim.status is PatientStatus.INACTIVE  # still wrong; still reported
