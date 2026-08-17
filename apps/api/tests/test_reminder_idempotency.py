"""Reminder idempotency (SPEC §6.2).

    "Test: run process_reminders three times in a row against the same seed; assert the
     reminder_events count is identical after runs 2 and 3, and the provider was called exactly
     once per eligible patient."

That is the test below, and it is the most consequential one in the suite. A duplicate reminder is
not a cosmetic bug: the patient receives two identical emails from their doctor, which reads as
either a mistake or as spam, and the practice's trust in the product does not recover.

What makes it work is that the guarantee is enforced by Postgres rather than by application code.
The unique index on ``(patient_id, reminder_rule_id, due_date_snapshot)`` is checked atomically at
insert. A "have I sent this?" query cannot be — two runs both read "no" before either writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from sqlalchemy.orm import Session

from app.email.provider import MockEmailProvider
from app.models.clinic_settings import ClinicSettings
from app.models.enums import (
    ReminderEventStatus,
    ReminderRuleKey,
    ReminderSource,
)
from app.models.organization import Organization
from app.models.reminder_event import ReminderEvent
from app.repositories.patients import PatientRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.services.recall import RecallService
from app.services.reminders import ReminderService
from tests.conftest import make_patient


@pytest.fixture
def provider() -> Iterator[MockEmailProvider]:
    """A mock provider with no delivery delay, so tests need not wait or freeze time."""
    mock = MockEmailProvider(delivery_delay_seconds=0.0)
    yield mock
    mock.clear()


@pytest.fixture
def reminders(db: Session, provider: MockEmailProvider) -> ReminderService:
    return ReminderService(db, provider=provider)


def seed_due_patients(
    db: Session, organization_id: uuid.UUID, count: int, *, days_until_due: int = 0
) -> list[uuid.UUID]:
    """Create patients due today (or a chosen offset), with derived fields written properly."""
    recall = RecallService(db)
    repo = PatientRepository(db)
    today = recall.today_for_org(organization_id)
    ids: list[uuid.UUID] = []

    for index in range(count):
        patient = make_patient(organization_id, first_name=f"Patient{index}")
        # 365 days before the due date, so a 12-month cycle lands the due date exactly where the
        # caller asked for it.
        patient.last_annual_visit_date = (
            today + timedelta(days=days_until_due) - timedelta(days=365)
        )
        repo.create(organization_id, patient)
        recall.apply_derived_fields(organization_id, patient)
        ids.append(patient.id)

    db.flush()
    return ids


@pytest.fixture
def org_with_rules(
    db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> Organization:
    ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()
    return organization


# ---------------------------------------------------------------------------------------------
# The test SPEC §6.2 asks for
# ---------------------------------------------------------------------------------------------


def test_three_consecutive_runs_send_exactly_once(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """THE IDEMPOTENCY TEST (SPEC §6.2), exactly as specified.

    Five patients are due today, so the T_ZERO rule fires for each. Runs 2 and 3 must create
    nothing, send nothing, and leave the row count untouched.
    """
    seed_due_patients(db, org_with_rules.id, count=5, days_until_due=0)

    first = reminders.process_reminders(org_with_rules.id)
    db.flush()
    count_after_first = db.query(ReminderEvent).count()

    second = reminders.process_reminders(org_with_rules.id)
    db.flush()
    count_after_second = db.query(ReminderEvent).count()

    third = reminders.process_reminders(org_with_rules.id)
    db.flush()
    count_after_third = db.query(ReminderEvent).count()

    # Run 1 does the work.
    assert first.created == 5
    assert first.sent == 5

    # Runs 2 and 3 do none of it.
    assert second.created == 0
    assert second.sent == 0
    assert third.created == 0
    assert third.sent == 0

    # The row count is stable — the assertion the spec names.
    assert count_after_first == count_after_second == count_after_third == 5

    # And exactly one email per eligible patient, across all three runs.
    assert provider.message_count == 5


def test_duplicates_are_reported_rather_than_silently_ignored(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
) -> None:
    """The job summary has to distinguish "nothing was due" from "it was already sent".

    Both produce ``created == 0``. Without ``skipped_duplicate``, an operator looking at a run
    that sent nothing cannot tell whether the job worked or whether eligibility is broken.
    """
    seed_due_patients(db, org_with_rules.id, count=3, days_until_due=0)

    reminders.process_reminders(org_with_rules.id)
    db.flush()
    second = reminders.process_reminders(org_with_rules.id)

    assert second.eligible == 3
    assert second.created == 0
    assert second.skipped_duplicate == 3


def test_a_duplicate_does_not_abort_the_rest_of_the_run(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """One already-sent patient must not stop the others being reminded.

    This is why the insert happens inside a SAVEPOINT. An IntegrityError poisons the enclosing
    transaction, so without ``begin_nested`` the first duplicate would abort every remaining
    patient in the run — and the failure would look like "the job stopped working" rather than
    "one row already existed".
    """
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=0)
    reminders.process_reminders(org_with_rules.id)
    db.flush()
    provider.clear()

    # Four more patients become due; the first is still a duplicate.
    seed_due_patients(db, org_with_rules.id, count=4, days_until_due=0)

    summary = reminders.process_reminders(org_with_rules.id)

    assert summary.skipped_duplicate == 1
    assert summary.created == 4
    assert provider.message_count == 4


def test_a_later_annual_cycle_is_a_separate_slot(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """``due_date_snapshot`` is in the key so next year's reminder is not blocked by this year's.

    Without it, a patient who was reminded and then seen would be permanently unable to receive
    that rule again — the row from the previous cycle would match forever.
    """
    recall = RecallService(db)
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=0)

    reminders.process_reminders(org_with_rules.id)
    db.flush()
    assert provider.message_count == 1

    patient = PatientRepository(db).list_all(org_with_rules.id)[0]
    first_snapshot = db.query(ReminderEvent).one().due_date_snapshot

    # The visit happens, so the due date rolls forward a full cycle.
    recall.mark_completed(org_with_rules.id, patient)
    db.flush()
    next_due = patient.next_annual_due_date
    assert next_due != first_snapshot

    # Actually move the clock to next year's due date, rather than rewinding the patient's dates.
    #
    # Rewinding does not work, and the reason is worth recording: the due date is derived from
    # the last visit date, so pushing the last visit back a year lands the due date on exactly
    # the date it had the first time round. The snapshot would match, the row would be a genuine
    # duplicate, and the test would be asserting the opposite of what it set out to.
    #
    # 18:00 UTC is late morning in the practice's timezone, so `today_for_org` lands on the
    # intended calendar date rather than the day before.
    with time_machine.travel(
        datetime(next_due.year, next_due.month, next_due.day, 18, 0, tzinfo=UTC), tick=False
    ):
        assert recall.today_for_org(org_with_rules.id) == next_due
        summary = reminders.process_reminders(org_with_rules.id)

    db.flush()

    assert summary.created == 1
    assert provider.message_count == 2

    snapshots = {event.due_date_snapshot for event in db.query(ReminderEvent).all()}
    assert snapshots == {first_snapshot, next_due}


# ---------------------------------------------------------------------------------------------
# The manual-send carve-out
# ---------------------------------------------------------------------------------------------


def test_a_manual_send_never_collides_with_a_rule_driven_one(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """The demo beat SPEC §6.2 protects.

    Staff press "Send Reminder" on a patient whose rule already fired today. With
    ``reminder_rule_id = NULL`` the unique index does not apply, so this succeeds rather than
    raising a duplicate-key error in front of a clinic owner.
    """
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=0)
    reminders.process_reminders(org_with_rules.id)
    db.flush()

    patient = PatientRepository(db).list_all(org_with_rules.id)[0]

    event = reminders.send_manual_reminder(org_with_rules.id, patient.public_id)
    db.flush()

    assert event.reminder_rule_id is None
    assert event.source is ReminderSource.MANUAL
    assert event.status is ReminderEventStatus.SENT
    assert provider.message_count == 2


def test_manual_sends_are_rate_limited_per_patient(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """SPEC §6.2: "Rate-limit manual sends per patient (e.g. 1 per hour) instead."

    Nothing else stops a staff member — or an impatient double-click — emailing the same person
    repeatedly, since the unique index deliberately does not apply to manual sends.
    """
    from app.services.reminders import ReminderThrottledError

    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=100)
    patient = PatientRepository(db).list_all(org_with_rules.id)[0]

    reminders.send_manual_reminder(org_with_rules.id, patient.public_id)
    db.flush()

    with pytest.raises(ReminderThrottledError):
        reminders.send_manual_reminder(org_with_rules.id, patient.public_id)

    assert provider.message_count == 1


def test_the_cooldown_is_per_patient_not_global(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """Sending to one patient must not block sending to a different one.

    A global cooldown would make the button unusable during exactly the workflow it exists for —
    working through a list of overdue patients.
    """
    seed_due_patients(db, org_with_rules.id, count=2, days_until_due=100)
    patients = PatientRepository(db).list_all(org_with_rules.id)

    reminders.send_manual_reminder(org_with_rules.id, patients[0].public_id)
    db.flush()
    reminders.send_manual_reminder(org_with_rules.id, patients[1].public_id)
    db.flush()

    assert provider.message_count == 2


def test_the_cooldown_expires(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
    provider: MockEmailProvider,
) -> None:
    """It is a cooldown, not a permanent block."""
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=100)
    patient = PatientRepository(db).list_all(org_with_rules.id)[0]

    first = reminders.send_manual_reminder(org_with_rules.id, patient.public_id)
    db.flush()

    # Backdate the send rather than waiting an hour.
    first.created_at = datetime.now(UTC) - timedelta(hours=2)
    db.flush()

    reminders.send_manual_reminder(org_with_rules.id, patient.public_id)
    db.flush()

    assert provider.message_count == 2


# ---------------------------------------------------------------------------------------------
# What the job records
# ---------------------------------------------------------------------------------------------


def test_the_rendered_message_is_stored_on_the_event(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
) -> None:
    """SPEC §6.4: "Store rendered messages so the app can display the exact email that went out."

    Re-rendering later would show what the template says *now*, which is not the same claim.
    """
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=0)

    reminders.process_reminders(org_with_rules.id)
    db.flush()

    event = db.query(ReminderEvent).one()

    assert event.rendered_subject
    assert event.rendered_body_html and "<html" in event.rendered_body_html.lower()
    assert event.rendered_body_text and "annual visit" in event.rendered_body_text


def test_the_rule_that_fired_is_recorded(
    db: Session,
    org_with_rules: Organization,
    reminders: ReminderService,
) -> None:
    """The timeline says which reminder this was, not merely that one was sent."""
    seed_due_patients(db, org_with_rules.id, count=1, days_until_due=0)

    reminders.process_reminders(org_with_rules.id)
    db.flush()

    event = db.query(ReminderEvent).one()

    assert event.reminder_rule is not None
    assert event.reminder_rule.key is ReminderRuleKey.T_ZERO
    assert event.source is ReminderSource.RULE
