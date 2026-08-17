"""Reminder eligibility and the catch-up window (SPEC §6.1).

Two directions of failure, and they are not symmetric.

**Too strict** and a practice loses the visits — and the revenue — this product exists to
recover. That failure is invisible: nothing appears on screen, and nobody notices the reminder
that was never sent.

**Too loose** and someone who asked not to be contacted receives an email from their doctor. That
failure is very visible indeed, and it is the one that ends a sale.

So each condition from SPEC §6.1 gets its own test, and the catch-up window is tested at both
edges.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.email.provider import MockEmailProvider
from app.models.clinic_settings import ClinicSettings
from app.models.enums import PatientStatus, ReminderRuleKey
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.reminder_rule import ReminderRule
from app.repositories.patients import PatientRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.services.recall import RecallService
from app.services.reminders import CATCH_UP_WINDOW_DAYS, ReminderService
from tests.conftest import make_patient


@pytest.fixture
def provider() -> Iterator[MockEmailProvider]:
    mock = MockEmailProvider(delivery_delay_seconds=0.0)
    yield mock
    mock.clear()


@pytest.fixture
def reminders(db: Session, provider: MockEmailProvider) -> ReminderService:
    return ReminderService(db, provider=provider)


@pytest.fixture
def rules(db: Session, organization: Organization) -> dict[ReminderRuleKey, ReminderRule]:
    created = ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()
    return {rule.key: rule for rule in created}


def patient_due_in(
    db: Session, organization_id: uuid.UUID, days: int, **overrides: object
) -> Patient:
    """A saved patient whose next visit is due ``days`` from the practice's today."""
    recall = RecallService(db)
    today = recall.today_for_org(organization_id)

    patient = make_patient(organization_id)
    patient.last_annual_visit_date = today + timedelta(days=days) - timedelta(days=365)
    PatientRepository(db).create(organization_id, patient)
    recall.apply_derived_fields(organization_id, patient)

    for field_name, value in overrides.items():
        setattr(patient, field_name, value)
    if overrides:
        recall.apply_derived_fields(organization_id, patient)

    db.flush()
    return patient


def today_for(db: Session, organization_id: uuid.UUID) -> object:
    return RecallService(db).today_for_org(organization_id)


# ---------------------------------------------------------------------------------------------
# The date test
# ---------------------------------------------------------------------------------------------


def test_a_rule_fires_on_its_target_date(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """T_MINUS_30 fires for a patient due in exactly 30 days."""
    patient = patient_due_in(db, organization.id, 30)

    assert reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_MINUS_30], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_a_rule_does_not_fire_before_its_target_date(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """One day early is not eligible. Firing ahead of the window would make the 30/7/0 schedule
    meaningless — every rule would fire as soon as it became true."""
    patient = patient_due_in(db, organization.id, 31)

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_MINUS_30], today_for(db, organization.id)
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule_key", "days_until_due"),
    [
        (ReminderRuleKey.T_MINUS_30, 30),
        (ReminderRuleKey.T_MINUS_7, 7),
        (ReminderRuleKey.T_ZERO, 0),
        (ReminderRuleKey.T_PLUS_30, -30),
    ],
)
def test_each_rule_fires_at_its_own_offset(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
    rule_key: ReminderRuleKey,
    days_until_due: int,
) -> None:
    """All four points of the campaign, each at the right moment."""
    patient = patient_due_in(db, organization.id, days_until_due)

    assert reminders.is_eligible(patient, rules[rule_key], today_for(db, organization.id))  # type: ignore[arg-type]


def test_only_one_rule_fires_on_any_given_day(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """A patient must not receive two reminders in one day.

    The offsets are 30 days apart, so this holds as long as the catch-up window stays well below
    that gap — which is a real constraint on ever widening it.
    """
    patient = patient_due_in(db, organization.id, 0)
    today = today_for(db, organization.id)

    eligible = [key for key, rule in rules.items() if reminders.is_eligible(patient, rule, today)]  # type: ignore[arg-type]

    assert eligible == [ReminderRuleKey.T_ZERO]


# ---------------------------------------------------------------------------------------------
# The catch-up window (SPEC §6.1)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("days_late", range(CATCH_UP_WINDOW_DAYS + 1))
def test_a_missed_run_is_caught_up_within_the_window(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
    days_late: int,
) -> None:
    """The job did not run on the target date — a machine was off, or a scheduler failed.

    Without the window, that reminder is lost forever: the target date never comes round again,
    and nothing anywhere records that it was missed.
    """
    patient = patient_due_in(db, organization.id, 0 - days_late)

    assert reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_the_catch_up_window_has_a_hard_edge(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """One day past the window, the reminder is genuinely gone.

    A reminder arriving a fortnight late is worse than none — it tells the patient the practice
    is disorganised, and by then the next rule in the campaign is closer anyway.
    """
    patient = patient_due_in(db, organization.id, -(CATCH_UP_WINDOW_DAYS + 1))

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_sarah_johnsons_t_zero_is_not_backfilled(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """SPEC §7.3 leaves the demo's opening patient with T_ZERO deliberately unsent.

    The whole live "Send Reminder" beat depends on there being something left to do. She is ~24
    days overdue, far outside the 3-day window, so the job cannot quietly backfill it and remove
    the demo's most important moment.

    Pinned here so that widening ``CATCH_UP_WINDOW_DAYS`` fails a test rather than the demo.
    """
    patient = patient_due_in(db, organization.id, -24)
    today = today_for(db, organization.id)

    assert patient.status is PatientStatus.OVERDUE
    assert not any(reminders.is_eligible(patient, rule, today) for rule in rules.values())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# Consent and status
# ---------------------------------------------------------------------------------------------


def test_a_paused_patient_is_never_eligible(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    patient = patient_due_in(db, organization.id, 0, reminders_enabled=False)

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_an_opted_out_patient_is_never_eligible(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """The single most important negative case in the file.

    Emailing someone who used the unsubscribe link is the failure that ends a sale, and unlike a
    missed reminder it is immediately visible to the person it harms.
    """
    patient = patient_due_in(db, organization.id, 0, opted_out_at=datetime.now(UTC))

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_a_patient_with_an_appointment_is_not_chased(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """They have already done what the reminder would ask them to do."""
    recall = RecallService(db)
    today = recall.today_for_org(organization.id)
    patient = patient_due_in(db, organization.id, 0)
    recall.mark_scheduled(organization.id, patient, today + timedelta(days=5))
    db.flush()

    assert patient.status is PatientStatus.SCHEDULED
    assert not reminders.is_eligible(patient, rules[ReminderRuleKey.T_ZERO], today)


def test_a_patient_with_no_email_is_skipped_quietly(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
) -> None:
    """Ineligible rather than failed: a missing address is a gap in the practice's records, not a
    delivery problem, and recording it as a failure would fill the recovery queue with rows
    nobody can act on by resending."""
    patient = patient_due_in(db, organization.id, 0)
    patient.email = "not-an-address"
    db.flush()

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]


def test_a_disabled_rule_never_fires(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """The toggles on the Reminders page have to actually stop mail going out."""
    patient = patient_due_in(db, organization.id, 0)
    rules[ReminderRuleKey.T_ZERO].enabled = False
    db.flush()

    assert not reminders.is_eligible(
        patient, rules[ReminderRuleKey.T_ZERO], today_for(db, organization.id)
    )  # type: ignore[arg-type]

    reminders.process_reminders(organization.id)
    assert provider.message_count == 0


# ---------------------------------------------------------------------------------------------
# Through the job, and across tenants
# ---------------------------------------------------------------------------------------------


def test_the_job_only_touches_its_own_organization(
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """A reminder job run for one practice must not email another practice's patients."""
    ReminderRuleRepository(db).create_default_rules(other_organization.id)
    db.flush()

    patient_due_in(db, organization.id, 0)
    patient_due_in(db, other_organization.id, 0)

    summary = reminders.process_reminders(organization.id)

    assert summary.sent == 1
    assert provider.message_count == 1


def test_the_job_recomputes_statuses_before_evaluating(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    rules: dict[ReminderRuleKey, ReminderRule],
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """Eligibility depends on status, and status goes stale simply because a day passed.

    A patient who became DUE overnight has a cached status of DUE_SOON until something recomputes
    it. Evaluating against that stale value would skip exactly the people the job exists to
    reach — silently, and only ever by one day, which is the hardest kind of bug to notice.
    """
    patient = patient_due_in(db, organization.id, 0)
    patient.status = PatientStatus.ACTIVE  # deliberately stale
    db.flush()

    summary = reminders.process_reminders(organization.id)

    assert summary.statuses_recomputed >= 1
    assert patient.status is PatientStatus.DUE
    assert summary.sent == 1
