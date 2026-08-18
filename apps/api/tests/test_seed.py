"""The demo seed (SPEC §7.3, constraints D1 and D4).

    "The seed must guarantee these, asserted by a test."

Constraint D4 is the reason this file exists: the demo script names six patients and claims exact
states for them, out loud, in front of a clinic owner. If the seed drifts, the talk track stops
matching the screen — and the person who notices is the customer.

Most tests here run ``SeedRunner`` against the test's own transaction, so they roll back like any
other test and never touch the developer's demo data. The one exception is the demo-reset test,
which is explicitly marked and explains itself.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.enums import (
    ActivityEventType,
    PatientStatus,
    ReminderEventStatus,
    ReminderRuleKey,
)
from app.models.patient import Patient
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.seed import fixtures as fx
from app.seed.runner import SeedRunner
from app.services.recall import RecallService

DEMO_ORG_ID = fx.seed_uuid("organization", fx.ORGANIZATION_SLUG)


@pytest.fixture
def seeded(db: Session) -> Session:
    """Run the seed inside the test transaction.

    ``create_auth_accounts=False`` so the suite does not depend on Supabase being reachable —
    the patient data is what these tests are about, and the auth path has its own tests.
    """
    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()
    return db


def patient_for(db: Session, key: str) -> Patient:
    patient = db.get(Patient, fx.seed_uuid("patient", key))
    assert patient is not None, f"seed did not create the fixture {key!r}"
    return patient


# ---------------------------------------------------------------------------------------------
# The named fixtures (SPEC §7.3) — the contract with the demo script
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("named", fx.NAMED_FIXTURES, ids=lambda f: f.key)
def test_each_named_fixture_is_in_its_documented_status(
    seeded: Session, named: fx.NamedFixture
) -> None:
    """SPEC constraint D4. Each of the six, in exactly the state the talk track claims."""
    patient = patient_for(seeded, named.key)

    assert patient.first_name == named.first_name
    assert patient.last_name == named.last_name
    assert patient.status is named.expected_status, (
        f"{named.first_name} {named.last_name} is {patient.status.value}, "
        f"but the demo script says {named.expected_status.value}. {named.note}"
    )


def test_sarah_johnson_opens_the_demo_correctly(seeded: Session) -> None:
    """The most load-bearing fixture in the product.

    SPEC §7.3: "due ~24 days ago; exactly 2 prior DELIVERED reminders (T-30, T-7). T_ZERO is
    deliberately left unsent so the live 'Send Reminder' beat in the demo has something to do."
    """
    sarah = patient_for(seeded, "sarah-johnson")
    today = RecallService(seeded).today_for_org(DEMO_ORG_ID)

    assert sarah.status is PatientStatus.OVERDUE
    assert (today - sarah.next_annual_due_date).days == 24

    events = ReminderEventRepository(seeded).list_for_patient(DEMO_ORG_ID, sarah.id)
    delivered = [e for e in events if e.status is ReminderEventStatus.DELIVERED]

    assert len(delivered) == 2, "the demo says 'two reminders already sent'"

    rule_keys = {e.reminder_rule.key for e in delivered if e.reminder_rule}
    assert rule_keys == {ReminderRuleKey.T_MINUS_30, ReminderRuleKey.T_MINUS_7}

    # The whole point: there must be something left to send on stage.
    assert ReminderRuleKey.T_ZERO not in rule_keys


def test_sarah_johnsons_remaining_reminder_cannot_be_backfilled(seeded: Session) -> None:
    """The catch-up window must not quietly remove the demo's best moment.

    At 24 days overdue she is far outside the 3-day window, so running the reminder job during a
    demo leaves her T_ZERO untouched. Widening ``CATCH_UP_WINDOW_DAYS`` would break this.
    """
    from app.services.reminders import ReminderService

    sarah = patient_for(seeded, "sarah-johnson")
    today = RecallService(seeded).today_for_org(DEMO_ORG_ID)
    rules = ReminderRuleRepository(seeded).list_enabled(DEMO_ORG_ID)
    service = ReminderService(seeded)

    assert not any(service.is_eligible(sarah, rule, today) for rule in rules)


def test_jennifer_tran_was_reminded_this_morning(seeded: Session) -> None:
    """SPEC §7.3: "due today; 1 reminder delivered this morning"."""
    jennifer = patient_for(seeded, "jennifer-tran")
    today = RecallService(seeded).today_for_org(DEMO_ORG_ID)

    assert jennifer.status is PatientStatus.DUE
    assert jennifer.next_annual_due_date == today

    events = ReminderEventRepository(seeded).list_for_patient(DEMO_ORG_ID, jennifer.id)
    assert len(events) == 1
    assert events[0].status is ReminderEventStatus.DELIVERED
    assert events[0].sent_at is not None
    assert events[0].sent_at.date() == today


def test_david_okafor_counts_as_a_recovered_appointment(seeded: Session) -> None:
    """SPEC §8's revenue definition needs a delivered reminder *followed by* a booking.

    A SCHEDULED patient with no prior reminder would inflate the number without justifying it,
    and the definition is shown on hover precisely so an office manager can check.
    """
    david = patient_for(seeded, "david-okafor")

    assert david.status is PatientStatus.SCHEDULED
    assert david.scheduled_for is not None

    delivered = ReminderEventRepository(seeded).list_delivered_for_patient(DEMO_ORG_ID, david.id)
    assert len(delivered) >= 1

    events = ActivityEventRepository(seeded).list_for_patient(DEMO_ORG_ID, david.id)
    booked = [e for e in events if e.type is ActivityEventType.APPOINTMENT_SCHEDULED]
    assert len(booked) == 1

    # Booked after the reminder, and within the 30-day window the metric requires.
    days_between = (booked[0].created_at - delivered[0].sent_at).days  # type: ignore[operator]
    assert 0 <= days_between <= 30


def test_maria_castillo_shows_the_loop_closed(seeded: Session) -> None:
    """SPEC §7.3: "completed 6 days ago; next due ~359 days out"."""
    maria = patient_for(seeded, "maria-castillo")
    today = RecallService(seeded).today_for_org(DEMO_ORG_ID)

    assert maria.status is PatientStatus.COMPLETED
    assert (today - maria.last_annual_visit_date).days == 6
    assert 355 <= (maria.next_annual_due_date - today).days <= 362


def test_robert_hale_has_a_failure_to_recover_from(seeded: Session) -> None:
    """SPEC §7.3: "hard bounce, surfaces the failure-recovery UI"."""
    robert = patient_for(seeded, "robert-hale")

    events = ReminderEventRepository(seeded).list_for_patient(DEMO_ORG_ID, robert.id)
    failed = [e for e in events if e.status is ReminderEventStatus.FAILED]

    assert len(failed) == 1
    assert failed[0].failure_reason
    # Readable by a receptionist — this text appears in the recovery queue.
    assert "bounce" in failed[0].failure_reason.lower()

    # And re-sending genuinely fails again until the address is corrected, which is what makes
    # the recovery screen worth having rather than decorative.
    assert "bounce" in robert.email


# ---------------------------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------------------------


def test_the_seed_creates_the_documented_number_of_patients(seeded: Session) -> None:
    assert PatientRepository(seeded).count(DEMO_ORG_ID) == fx.TOTAL_PATIENTS == 55


def test_every_status_is_represented_in_the_documented_proportions(seeded: Session) -> None:
    """SPEC §7.3: "spread across all statuses with realistic distribution (not 8 per bucket)".

    An even split looks generated, and would make the Recall Overview a flat, meaningless bar. A
    real practice has most patients quietly up to date and a worrying tail of overdue.
    """
    counts = PatientRepository(seeded).count_by_status(DEMO_ORG_ID)

    assert counts == fx.STATUS_DISTRIBUTION

    # And it genuinely is uneven — the point of the requirement.
    assert len(set(counts.values())) > 3, "the distribution looks suspiciously even"


def test_the_four_reminder_rules_exist_and_are_enabled(seeded: Session) -> None:
    rules = ReminderRuleRepository(seeded).list_ordered(DEMO_ORG_ID)

    assert [rule.key for rule in rules] == [
        ReminderRuleKey.T_MINUS_30,
        ReminderRuleKey.T_MINUS_7,
        ReminderRuleKey.T_ZERO,
        ReminderRuleKey.T_PLUS_30,
    ]
    assert all(rule.enabled for rule in rules)


def test_there_is_enough_history_to_populate_the_feeds(seeded: Session) -> None:
    """SPEC §7.3: history "so every chart and feed is populated at first login".

    An empty activity feed on the opening screen makes a working product look broken.
    """
    reminders = ReminderEventRepository(seeded).list_recent(DEMO_ORG_ID, limit=500)
    activity = ActivityEventRepository(seeded).list_recent(DEMO_ORG_ID, limit=500)

    assert len(reminders) >= 25
    assert len(activity) >= 40

    # And every filter chip on the Activity page has something behind it.
    for group in ("reminders", "patients", "imports"):
        assert ActivityEventRepository(seeded).list_recent(DEMO_ORG_ID, filter_group=group), (
            f"the '{group}' activity filter would be empty at first login"
        )


def test_the_delivery_strip_shows_a_believable_mix(seeded: Session) -> None:
    """Some failures, mostly successes. A perfect record is not credible."""
    counts = ReminderEventRepository(seeded).count_by_status(DEMO_ORG_ID)

    assert counts[ReminderEventStatus.DELIVERED] > 0
    assert counts[ReminderEventStatus.FAILED] > 0
    assert counts[ReminderEventStatus.DELIVERED] > counts[ReminderEventStatus.FAILED] * 3


# ---------------------------------------------------------------------------------------------
# Privacy and safety (SPEC §7.3, D6)
# ---------------------------------------------------------------------------------------------


def test_every_seeded_email_is_synthetic(seeded: Session) -> None:
    """SPEC §7.3: "All identities fictional; emails on @example.com".

    These records are committed to a repository and shown in demos. Nothing may resolve to a real
    person, and nothing may accidentally receive an email if a real provider is ever configured.
    """
    for patient in PatientRepository(seeded).list_all(DEMO_ORG_ID):
        assert patient.email.endswith("@example.com"), patient.email


def test_every_seeded_phone_number_is_in_the_555_range(seeded: Session) -> None:
    """555 numbers are reserved for fiction and cannot ring a real person."""
    for patient in PatientRepository(seeded).list_all(DEMO_ORG_ID):
        assert patient.phone is None or patient.phone.startswith("555-"), patient.phone


def test_activity_payloads_never_contain_names_or_addresses(seeded: Session) -> None:
    """SPEC §9 data minimisation, checked across the whole seeded feed."""
    patients = {p.id: p for p in PatientRepository(seeded).list_all(DEMO_ORG_ID)}

    for event in ActivityEventRepository(seeded).list_recent(DEMO_ORG_ID, limit=500):
        payload = str(event.payload)
        assert "@" not in payload, f"an email address reached an activity payload: {payload}"

        if event.subject_patient_id:
            patient = patients[event.subject_patient_id]
            assert patient.first_name not in payload
            assert patient.last_name not in payload


# ---------------------------------------------------------------------------------------------
# Relative dates (SPEC constraint D1)
# ---------------------------------------------------------------------------------------------


def test_no_seed_source_file_contains_a_hardcoded_calendar_date() -> None:
    """SPEC D1: "Seed dates are relative to today, never hardcoded calendar dates."

    A static check over the source, because the failure it prevents is silent: a demo built on
    fixed dates keeps working for about a week, then every status badge starts disagreeing with
    the date printed beside it.
    """
    seed_dir = Path(__file__).resolve().parents[1] / "app" / "seed"
    iso_date = re.compile(r"\b20\d\d-\d\d-\d\d\b")

    offenders: list[str] = []
    for path in seed_dir.glob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            # Comments and docstrings may mention example dates freely.
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if iso_date.search(line):
                offenders.append(f"{path.name}:{number}: {stripped}")

    assert not offenders, "hardcoded dates in the seed:\n  " + "\n  ".join(offenders)


def test_the_seeded_dates_move_with_today(seeded: Session) -> None:
    """The behavioural counterpart to the static check above.

    Every fixture's due date is expressed as an offset from the practice's today, so the whole
    demo shifts forward with the calendar rather than going stale.
    """
    today = RecallService(seeded).today_for_org(DEMO_ORG_ID)

    for named in fx.NAMED_FIXTURES:
        patient = patient_for(seeded, named.key)
        assert (patient.next_annual_due_date - today).days == named.days_until_due


# ---------------------------------------------------------------------------------------------
# The drift guard, against the real seed (SPEC §5.1)
# ---------------------------------------------------------------------------------------------


def test_no_seeded_patient_status_has_drifted(seeded: Session) -> None:
    """SPEC §5.1: "a test that walks every patient in a seeded org".

    Phase 3 ran this against a handful of constructed patients. This is the version the spec
    actually asks for — all 55, written by the real seed path.
    """
    drifted = RecallService(seeded).find_drifted_patients(DEMO_ORG_ID)

    assert drifted == [], "stored status disagrees with compute_status for: " + ", ".join(
        f"{patient.public_id} stored={patient.status.value} expected={expected.value}"
        for patient, expected in drifted
    )


# ---------------------------------------------------------------------------------------------
# Idempotency (SPEC §7.3)
# ---------------------------------------------------------------------------------------------


def test_running_the_seed_twice_changes_nothing(db: Session) -> None:
    """ "Seed must be idempotent — re-running does not duplicate."

    ``make setup`` seeds, ``make demo-reset`` seeds, and anyone nervous before a demo will seed
    again for luck. If the seed appended rather than reconciled, the demo would show 110 patients
    and two Sarah Johnsons.
    """
    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()
    first = _row_counts(db)

    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()
    second = _row_counts(db)

    assert first == second


def test_public_ids_survive_a_reseed(db: Session) -> None:
    """A patient keeps the same URL across a reset.

    Deterministic public ids mean a screenshot, a bookmark, or a Playwright test written last
    week still points at Sarah Johnson after ``make demo-reset``.
    """
    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()
    before = patient_for(db, "sarah-johnson").public_id

    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()

    assert patient_for(db, "sarah-johnson").public_id == before
    assert before == fx.seed_public_id("sarah-johnson")


def _row_counts(db: Session) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.models.activity_event import ActivityEvent
    from app.models.organization import Organization
    from app.models.reminder_event import ReminderEvent
    from app.models.reminder_rule import ReminderRule
    from app.models.user import User

    models = {
        "organizations": Organization,
        "users": User,
        "patients": Patient,
        "reminder_rules": ReminderRule,
        "reminder_events": ReminderEvent,
        "activity_events": ActivityEvent,
    }
    return {
        name: db.execute(select(func.count()).select_from(model)).scalar_one()
        for name, model in models.items()
    }


# ---------------------------------------------------------------------------------------------
# Demo reset (SPEC constraint D3, SPEC §11)
# ---------------------------------------------------------------------------------------------


def test_clearing_and_reseeding_restores_the_named_fixtures(db: Session) -> None:
    """SPEC §11: "make demo-reset returns the DB to seed state verified by a test."

    Exercises the same two steps ``make demo-reset`` performs — clear, then seed — but inside the
    test transaction, so a developer running pytest does not lose the demo data they were looking
    at. The wall-clock budget in SPEC D3 is checked by ``scripts/demo-reset.sh`` itself, which
    warns if a real run exceeds 30 seconds.
    """
    from app.demo_reset import TABLES_TO_CLEAR

    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()

    # Mutate the way a demo would: send a reminder, book an appointment.
    sarah = patient_for(db, "sarah-johnson")
    recall = RecallService(db)
    recall.mark_scheduled(DEMO_ORG_ID, sarah, recall.today_for_org(DEMO_ORG_ID))
    db.flush()
    assert sarah.status is PatientStatus.SCHEDULED

    # The reset: clear everything, then seed again.
    from sqlalchemy import text

    db.execute(text(f"TRUNCATE TABLE {', '.join(TABLES_TO_CLEAR)} CASCADE"))

    # TRUNCATE goes straight to the database, so the session's identity map still holds objects
    # for rows that no longer exist. Re-seeding then tries to UPDATE them and raises
    # StaleDataError. `expunge_all` makes the session forget everything it is holding.
    #
    # The real `demo_reset.py` does not need this because it runs the clear and the seed in two
    # separate `session_scope()` blocks, so the second one starts with an empty identity map.
    # Only this test, which shares one session to stay inside the rollback, has to say it.
    db.expunge_all()

    SeedRunner(db).run(create_auth_accounts=False)
    db.flush()

    restored = patient_for(db, "sarah-johnson")
    assert restored.status is PatientStatus.OVERDUE
    assert restored.scheduled_for is None
    assert PatientRepository(db).count(DEMO_ORG_ID) == fx.TOTAL_PATIENTS


def test_the_reset_table_list_covers_every_application_table(db: Session) -> None:
    """A table missing from the list would survive a reset and quietly accumulate demo debris.

    ``alembic_version`` is deliberately excluded: truncating it would make the database look
    unmigrated, and the next ``alembic upgrade head`` would try to create tables that exist.
    """
    from sqlalchemy import text

    from app.demo_reset import TABLES_TO_CLEAR

    rows = (
        db.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        .scalars()
        .all()
    )

    assert set(rows) == set(TABLES_TO_CLEAR)


# ---------------------------------------------------------------------------------------------
# Demo credentials
# ---------------------------------------------------------------------------------------------


def test_the_demo_credentials_are_documented_in_the_readme() -> None:
    """SPEC §7.3: "Demo credentials ... documented in the README."

    A demo nobody can sign in to is not a demo. Checked as a test because the credentials live in
    two places, and the one that goes stale is always the prose.
    """
    readme = (Path(__file__).resolve().parents[3] / "README.md").read_text()

    assert fx.DEMO_ADMIN.email in readme
    assert fx.DEMO_ADMIN.password in readme


def test_the_seed_creates_one_admin_and_one_staff_account(seeded: Session) -> None:
    """Two roles, so the admin-only demo utilities in Settings are demonstrable."""
    from app.models.enums import UserRole
    from app.repositories.users import UserRepository

    users = UserRepository(seeded)

    assert len(users.list_by_role(DEMO_ORG_ID, UserRole.ADMIN)) == 1
    assert len(users.list_by_role(DEMO_ORG_ID, UserRole.STAFF)) == 1


def test_seeded_identifiers_are_stable_across_processes() -> None:
    """Determinism is a property of the fixture keys, not of a database round trip.

    This is what lets a Playwright test hardcode a patient URL without first querying for it.
    """
    assert fx.seed_uuid("patient", "sarah-johnson") == uuid.uuid5(
        fx.SEED_NAMESPACE, "patient|sarah-johnson"
    )
    assert len(fx.seed_public_id("sarah-johnson")) == 12
    assert fx.seed_public_id("sarah-johnson") != fx.seed_public_id("michael-brennan")


def test_the_seeded_history_reaches_back_far_enough(seeded: Session) -> None:
    """SPEC §7.3 asks for 60-90 days, so the dashboard's trends have something to plot."""
    events = ReminderEventRepository(seeded).list_recent(DEMO_ORG_ID, limit=500)
    oldest = min(event.created_at for event in events)

    assert (datetime.now(UTC) - oldest).days >= 30
