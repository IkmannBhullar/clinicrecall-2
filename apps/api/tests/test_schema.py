"""Schema guarantees.

These tests check the constraints the *database* enforces, as opposed to the rules the
application enforces. That distinction matters: a constraint in Postgres holds regardless of
which code path wrote the row — including a migration, a bulk import, or someone in psql — while
a check written in Python only holds for code that remembers to call it.

The reminder-event tests near the bottom are the most load-bearing in this file. They pin down
the behaviour SPEC §6.2 depends on for idempotency, and the ``NULL`` carve-out that keeps the
live "Send Reminder" moment in the demo from failing on stage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ids import PUBLIC_ID_LENGTH, generate_public_id
from app.models import (
    ClinicSettings,
    Organization,
    Patient,
    ReminderEvent,
    ReminderRule,
    ReminderRuleKey,
    ReminderSource,
)
from app.repositories import PatientRepository, ReminderRuleRepository
from tests.conftest import make_patient

# ---------------------------------------------------------------------------------------------
# Public IDs (SPEC §4.2)
# ---------------------------------------------------------------------------------------------


def test_generate_public_id_has_the_expected_shape() -> None:
    public_id = generate_public_id()

    assert len(public_id) == PUBLIC_ID_LENGTH
    assert public_id.isupper() or public_id.isalnum()


def test_public_ids_exclude_ambiguous_characters() -> None:
    """I, L, O and U are omitted so an ID can be read aloud or copied without error.

    Generated over many samples because a single ID proves nothing about the alphabet.
    """
    sample = "".join(generate_public_id() for _ in range(200))

    for character in "ILOU":
        assert character not in sample


def test_public_ids_are_unique_in_practice() -> None:
    """60 bits of entropy: a collision in a thousand draws would indicate a broken generator."""
    generated = {generate_public_id() for _ in range(1000)}

    assert len(generated) == 1000


def test_patient_gets_a_public_id_automatically(db: Session, organization: Organization) -> None:
    """Nothing should have to remember to assign one."""
    patient = make_patient(organization.id)
    db.add(patient)
    db.flush()

    assert patient.public_id is not None
    assert len(patient.public_id) == PUBLIC_ID_LENGTH


# ---------------------------------------------------------------------------------------------
# Patient dedupe keys (SPEC §7.1)
# ---------------------------------------------------------------------------------------------


def test_email_uniqueness_is_case_insensitive(db: Session, organization: Organization) -> None:
    """ "Sarah.Johnson@Example.com" and "sarah.johnson@example.com" are the same person.

    Enforced by a functional index on ``lower(email)`` rather than by normalising on write, so
    the guarantee holds even for rows the application did not create.
    """
    repo = PatientRepository(db)
    repo.create(organization.id, make_patient(organization.id, email="Sarah.Johnson@Example.com"))
    db.flush()

    repo.create(organization.id, make_patient(organization.id, email="sarah.johnson@example.com"))

    with pytest.raises(IntegrityError):
        db.flush()


def test_the_same_email_may_exist_in_two_organizations(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """Someone who moves house and joins a new practice is the normal case, not an error."""
    email = "shared.person@example.com"
    repo = PatientRepository(db)

    repo.create(organization.id, make_patient(organization.id, email=email))
    repo.create(other_organization.id, make_patient(other_organization.id, email=email))

    db.flush()  # must not raise


def test_external_id_is_unique_within_an_organization(
    db: Session, organization: Organization
) -> None:
    repo = PatientRepository(db)
    repo.create(organization.id, make_patient(organization.id, external_id="MRN-001"))
    db.flush()

    repo.create(organization.id, make_patient(organization.id, external_id="MRN-001"))

    with pytest.raises(IntegrityError):
        db.flush()


def test_many_patients_may_have_no_external_id(db: Session, organization: Organization) -> None:
    """The index is partial (``WHERE external_id IS NOT NULL``) for exactly this reason.

    Plenty of CSV exports carry no practice identifier, and those patients must not collide with
    each other.
    """
    repo = PatientRepository(db)
    for _ in range(5):
        repo.create(organization.id, make_patient(organization.id, external_id=None))

    db.flush()  # must not raise

    assert repo.count(organization.id) == 5


# ---------------------------------------------------------------------------------------------
# Reminder idempotency (SPEC §6.2) — the most important constraints in the schema
# ---------------------------------------------------------------------------------------------


def _make_event(
    organization_id: uuid.UUID,
    patient: Patient,
    *,
    rule: ReminderRule | None,
    due_date: date,
    source: ReminderSource = ReminderSource.RULE,
) -> ReminderEvent:
    return ReminderEvent(
        organization_id=organization_id,
        patient_id=patient.id,
        reminder_rule_id=rule.id if rule is not None else None,
        source=source,
        due_date_snapshot=due_date,
        scheduled_at=datetime.now(UTC),
    )


def test_the_same_rule_cannot_fire_twice_for_one_due_date(
    db: Session, organization: Organization
) -> None:
    """This unique index is the entire idempotency guarantee.

    SPEC §6.2 forbids implementing idempotency as an application-level "have I sent this?"
    query, because two concurrent job runs can both read "no" before either writes. The database
    arbitrates instead, so timing cannot produce a duplicate email.
    """
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    rules = ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    rule = next(r for r in rules if r.key is ReminderRuleKey.T_MINUS_30)
    due = datetime.now(UTC).date() + timedelta(days=30)

    db.add(_make_event(organization.id, patient, rule=rule, due_date=due))
    db.flush()

    db.add(_make_event(organization.id, patient, rule=rule, due_date=due))

    with pytest.raises(IntegrityError):
        db.flush()


def test_the_same_rule_may_fire_again_for_a_later_due_date(
    db: Session, organization: Organization
) -> None:
    """``due_date_snapshot`` is in the key so each annual cycle is a separate slot.

    Without it, a patient who completed a visit and rolled forward to next year's due date could
    never receive another 30-day reminder — the row from last year would block it forever.
    """
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    rules = ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    rule = next(r for r in rules if r.key is ReminderRuleKey.T_MINUS_30)
    this_year = datetime.now(UTC).date() + timedelta(days=30)
    next_year = this_year + timedelta(days=365)

    db.add(_make_event(organization.id, patient, rule=rule, due_date=this_year))
    db.add(_make_event(organization.id, patient, rule=rule, due_date=next_year))

    db.flush()  # must not raise


def test_manual_sends_never_collide(db: Session, organization: Organization) -> None:
    """The carve-out that keeps the live demo from failing on stage (SPEC §6.2).

    A manual send carries ``reminder_rule_id = NULL``. Postgres does not treat two NULLs as equal
    in a unique index, so any number of manual sends coexist — with each other and with a
    rule-driven reminder for the same patient and the same due date.

    Without this, pressing "Send Reminder" during a demo on a patient whose rule had already
    fired would raise a duplicate-key error in front of a clinic owner.
    """
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    rules = ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    rule = next(r for r in rules if r.key is ReminderRuleKey.T_ZERO)
    due = datetime.now(UTC).date()

    # The scheduled rule already fired for this due date.
    db.add(_make_event(organization.id, patient, rule=rule, due_date=due))
    db.flush()

    # Staff now press "Send Reminder" three times. None of these may fail.
    for _ in range(3):
        db.add(
            _make_event(
                organization.id,
                patient,
                rule=None,
                due_date=due,
                source=ReminderSource.MANUAL,
            )
        )

    db.flush()  # must not raise

    events = db.query(ReminderEvent).filter(ReminderEvent.patient_id == patient.id).all()
    assert len(events) == 4
    assert sum(1 for e in events if e.source is ReminderSource.MANUAL) == 3


# ---------------------------------------------------------------------------------------------
# Clinic settings constraints
# ---------------------------------------------------------------------------------------------


def test_annual_interval_must_be_positive(db: Session, organization: Organization) -> None:
    """A zero or negative interval would make every patient permanently overdue."""
    db.add(
        ClinicSettings(
            organization_id=organization.id, clinic_name="Test Clinic", annual_interval_months=0
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_estimated_visit_value_cannot_be_negative(db: Session, organization: Organization) -> None:
    db.add(
        ClinicSettings(
            organization_id=organization.id,
            clinic_name="Test Clinic",
            estimated_annual_visit_value=Decimal("-1.00"),
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_visit_value_is_exact_not_floating_point(
    db: Session, clinic_settings: ClinicSettings
) -> None:
    """Currency is stored as NUMERIC, so arithmetic an office manager checks by hand agrees.

    In binary floating point 0.1 + 0.2 is not 0.3, and a revenue figure that is a cent out is a
    revenue figure nobody trusts.
    """
    clinic_settings.estimated_annual_visit_value = Decimal("250.10")
    db.flush()
    db.refresh(clinic_settings)

    assert clinic_settings.estimated_annual_visit_value == Decimal("250.10")
    assert isinstance(clinic_settings.estimated_annual_visit_value, Decimal)


def test_one_settings_row_per_organization(db: Session, organization: Organization) -> None:
    db.add(ClinicSettings(organization_id=organization.id, clinic_name="First"))
    db.flush()

    db.add(ClinicSettings(organization_id=organization.id, clinic_name="Second"))

    with pytest.raises(IntegrityError):
        db.flush()


# ---------------------------------------------------------------------------------------------
# Cascade behaviour
# ---------------------------------------------------------------------------------------------


def test_deleting_an_organization_removes_its_patients(db: Session) -> None:
    """Needed by ``make demo-reset``, which clears a tenant and reloads it (SPEC D3)."""
    org = Organization(name="Temporary Clinic", slug=f"temp-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()

    patient = make_patient(org.id)
    db.add(patient)
    db.flush()
    patient_id = patient.id

    db.delete(org)
    db.flush()

    assert db.get(Patient, patient_id) is None


def test_deleting_a_patient_removes_their_reminder_events(
    db: Session, organization: Organization
) -> None:
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    rules = ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    event = _make_event(organization.id, patient, rule=rules[0], due_date=datetime.now(UTC).date())
    db.add(event)
    db.flush()
    event_id = event.id

    db.delete(patient)
    db.flush()

    assert db.get(ReminderEvent, event_id) is None


# ---------------------------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------------------------


def test_new_patients_are_contactable_by_default(db: Session, organization: Organization) -> None:
    patient = make_patient(organization.id)
    db.add(patient)
    db.flush()

    assert patient.reminders_enabled is True
    assert patient.opted_out_at is None
    assert patient.is_contactable is True


def test_is_contactable_reflects_both_ways_reminders_stop(
    db: Session, organization: Organization
) -> None:
    """A staff pause and a patient opt-out are independent; either one is sufficient to stop."""
    patient = make_patient(organization.id)
    db.add(patient)
    db.flush()

    patient.reminders_enabled = False
    assert patient.is_contactable is False

    patient.reminders_enabled = True
    patient.opted_out_at = datetime.now(UTC)
    assert patient.is_contactable is False


def test_initials_are_used_instead_of_names_in_logs(
    db: Session, organization: Organization
) -> None:
    """SPEC §9: activity payloads carry initials, never names."""
    patient = make_patient(organization.id, first_name="Sarah", last_name="Johnson")

    assert patient.initials == "SJ"
