"""Settings, campaign rules, activity, and the recovery path (SPEC §8).

Two things here carry weight beyond "the endpoint returns 200".

**Changing the recall interval re-derives every patient.** It is not a preference — it moves every
due date and every status badge in the practice. A change that saved but did not recompute would
leave the settings page and the patient list disagreeing, with nothing on screen to say which was
right.

**The demo utilities are fenced.** Admin-only, and absent entirely in production. One of them
emails patients on demand and the other wipes the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_token_claims
from app.core.security import TokenClaims
from app.email.provider import MockEmailProvider, set_email_provider
from app.main import app
from app.models.clinic_settings import ClinicSettings
from app.models.enums import ActivityEventType, ReminderRuleKey, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.services.recall import RecallService
from tests.conftest import make_patient


@pytest.fixture
def provider() -> Iterator[MockEmailProvider]:
    mock = MockEmailProvider(delivery_delay_seconds=0.0)
    set_email_provider(mock)
    yield mock
    set_email_provider(None)


def client_for(db: Session, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_token_claims] = lambda: TokenClaims(
        subject=user.auth_user_id,
        email=user.email,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return TestClient(app)


@pytest.fixture
def admin_client(
    db: Session, staff_user: User, clinic_settings: ClinicSettings, provider: MockEmailProvider
) -> Iterator[TestClient]:
    """`staff_user` is seeded as an ADMIN — see tests/conftest.py."""
    with client_for(db, staff_user) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def staff_only_client(
    db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> Iterator[TestClient]:
    """A non-admin, for checking what the STAFF role cannot do."""
    import uuid

    user = User(
        organization_id=organization.id,
        auth_user_id=uuid.uuid4(),
        first_name="Jordan",
        last_name="Reyes",
        email=f"jordan+{uuid.uuid4().hex[:6]}@example.com",
        role=UserRole.STAFF,
    )
    db.add(user)
    db.flush()

    with client_for(db, user) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------


def test_the_settings_page_returns_everything_it_renders(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    body = admin_client.get("/settings").json()

    assert body["clinic"]["clinic_name"]
    assert len(body["rules"]) == 4
    assert body["account"]["role"] == "ADMIN"
    assert body["demo_utilities_enabled"] is True


def test_updating_the_recall_interval_recomputes_every_patient(
    admin_client: TestClient,
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
) -> None:
    """The change that is not a preference.

    A patient 400 days past their last visit is overdue on a 12-month cycle and comfortably
    up to date on a 24-month one. If the setting saved without recomputing, the settings page
    would say 24 months while every badge still reflected 12.
    """
    recall = RecallService(db)
    today = recall.today_for_org(organization.id)

    patient = make_patient(organization.id)
    patient.last_annual_visit_date = today - timedelta(days=400)
    PatientRepository(db).create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()

    before = patient.next_annual_due_date

    response = admin_client.patch("/settings", json={"annual_interval_months": 24})

    assert response.status_code == 200
    db.refresh(patient)
    assert patient.next_annual_due_date != before
    assert patient.next_annual_due_date > today


def test_an_invalid_timezone_is_refused(admin_client: TestClient) -> None:
    """Accepting one would not fail here — it would fail silently later.

    ``today_for_timezone`` falls back to a default rather than raising, so every date in the
    product would be computed against the wrong timezone with nothing on screen to say so.
    """
    response = admin_client.patch("/settings", json={"timezone": "Mars/Olympus_Mons"})

    assert response.status_code == 422


def test_settings_changes_are_recorded_without_the_values(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    """SPEC §9: the audit payload holds field names, never the practice's contact details."""
    admin_client.patch("/settings", json={"phone": "555-0199"})

    events = ActivityEventRepository(db).list_by_type(
        organization.id, ActivityEventType.SETTINGS_UPDATED
    )

    assert events
    assert events[0].payload["fields"] == ["phone"]
    assert "555-0199" not in str(events[0].payload)


def test_a_staff_user_cannot_change_settings(staff_only_client: TestClient) -> None:
    """Enforced by the API, not merely hidden in the interface."""
    response = staff_only_client.patch("/settings", json={"clinic_name": "Renamed"})

    assert response.status_code == 403


def test_a_staff_user_can_still_read_settings(staff_only_client: TestClient) -> None:
    """Being able to check the phone number in your own reminders is useful.

    Hiding the whole page would be a worse trade than making it read-only.
    """
    body = staff_only_client.get("/settings").json()

    assert body["account"]["role"] == "STAFF"
    assert body["clinic"]["clinic_name"]


def test_the_visit_value_stays_exact(
    admin_client: TestClient, db: Session, clinic_settings: ClinicSettings
) -> None:
    """Currency is NUMERIC end to end — an office manager checking the arithmetic must agree."""
    admin_client.patch("/settings", json={"estimated_annual_visit_value": "312.50"})
    db.refresh(clinic_settings)

    assert clinic_settings.estimated_annual_visit_value == Decimal("312.50")


# ---------------------------------------------------------------------------------------------
# Campaign rules (SPEC §8: toggles only)
# ---------------------------------------------------------------------------------------------


def test_a_rule_can_be_turned_off_and_on(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    off = admin_client.patch("/reminders/rules/T_PLUS_30", json={"enabled": False}).json()
    assert off["enabled"] is False

    on = admin_client.patch("/reminders/rules/T_PLUS_30", json={"enabled": True}).json()
    assert on["enabled"] is True


def test_a_rules_offset_cannot_be_changed(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    """SPEC §8: "enable/disable toggles only — no automation builder".

    A configurable schedule is a workflow builder in disguise, and SPEC §1 puts those out of
    scope. Extra fields are ignored rather than applied.
    """
    ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()

    admin_client.patch(
        "/reminders/rules/T_MINUS_7", json={"enabled": True, "days_relative_to_due_date": -99}
    )

    rule = ReminderRuleRepository(db).get_by_key(organization.id, ReminderRuleKey.T_MINUS_7)
    assert rule is not None
    assert rule.days_relative_to_due_date == -7


def test_a_disabled_rule_is_reflected_in_the_list(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    ReminderRuleRepository(db).create_default_rules(organization.id)
    db.flush()
    admin_client.patch("/reminders/rules/T_ZERO", json={"enabled": False})

    rules = {r["key"]: r["enabled"] for r in admin_client.get("/reminders/rules").json()}

    assert rules["T_ZERO"] is False
    assert rules["T_MINUS_7"] is True


# ---------------------------------------------------------------------------------------------
# Activity feed (SPEC §8)
# ---------------------------------------------------------------------------------------------


def test_the_feed_shows_initials_never_names(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    """SPEC §8: "initials rather than full names in the high-level list".

    This is the page most likely to be left open on a shared monitor at a front desk.
    """
    recall = RecallService(db)
    patient = make_patient(organization.id, first_name="Sarah", last_name="Johnson")
    PatientRepository(db).create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()
    recall.mark_scheduled(
        organization.id, patient, recall.today_for_org(organization.id) + timedelta(days=5)
    )
    db.flush()

    body = admin_client.get("/activity").json()
    serialised = str(body)

    assert "Sarah" not in serialised
    assert "Johnson" not in serialised
    assert any(entry["patient_initials"] == "SJ" for entry in body["entries"])


def test_the_feed_summaries_are_readable_not_enum_names(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    """An enum leaking onto the screen is a small failure of care that people notice."""
    ActivityEventRepository(db).record(
        organization.id,
        event_type=ActivityEventType.PATIENT_IMPORTED,
        payload={"created": 12, "skipped": 3},
    )
    db.flush()

    summary = admin_client.get("/activity").json()["entries"][0]["summary"]

    assert "PATIENT_IMPORTED" not in summary
    assert "12 added" in summary
    assert "3 skipped" in summary


def test_the_feed_filters(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    repository = ActivityEventRepository(db)
    repository.record(organization.id, event_type=ActivityEventType.PATIENT_IMPORTED)
    repository.record(organization.id, event_type=ActivityEventType.REMINDER_DELIVERED)
    db.flush()

    assert len(admin_client.get("/activity?filter=imports").json()["entries"]) == 1
    assert len(admin_client.get("/activity?filter=reminders").json()["entries"]) == 1
    assert len(admin_client.get("/activity").json()["entries"]) == 2


def test_the_feed_is_scoped_to_one_practice(
    admin_client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
) -> None:
    ActivityEventRepository(db).record(
        other_organization.id, event_type=ActivityEventType.PATIENT_IMPORTED
    )
    db.flush()

    assert admin_client.get("/activity").json()["entries"] == []


# ---------------------------------------------------------------------------------------------
# The failure-recovery path (SPEC §8)
# ---------------------------------------------------------------------------------------------


def test_fixing_an_address_corrects_it_and_resends(
    admin_client: TestClient,
    db: Session,
    organization: Organization,
    provider: MockEmailProvider,
) -> None:
    """A hard bounce means the address is wrong, so "retry" alone would fail identically forever.

    Correcting and resending is one intention, so it is one action.
    """
    recall = RecallService(db)
    patient = make_patient(organization.id, email="bounce.address@example.com")
    PatientRepository(db).create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()

    # A send to an address containing "bounce" is rejected by the mock provider.
    from app.services.reminders import ReminderService

    ReminderService(db).send_manual_reminder(organization.id, patient.public_id)
    db.flush()

    failed = admin_client.get("/reminders/failed").json()
    assert len(failed) == 1

    result = admin_client.post(
        f"/reminders/{failed[0]['id']}/fix-email",
        json={"email": "corrected@example.com", "resend": True},
    ).json()

    db.refresh(patient)
    assert patient.email == "corrected@example.com"
    assert result["resent"] is True
    assert admin_client.get("/reminders/failed").json() == []


def test_the_recovery_queue_is_scoped(
    admin_client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    provider: MockEmailProvider,
) -> None:
    from app.services.reminders import ReminderService

    recall = RecallService(db)
    theirs = make_patient(other_organization.id, email="bounce.theirs@example.com")
    PatientRepository(db).create(other_organization.id, theirs)
    recall.apply_derived_fields(other_organization.id, theirs)
    db.flush()
    ReminderService(db).send_manual_reminder(other_organization.id, theirs.public_id)
    db.flush()

    assert admin_client.get("/reminders/failed").json() == []


def test_a_test_reminder_is_recorded_as_a_test(
    admin_client: TestClient,
    db: Session,
    organization: Organization,
    provider: MockEmailProvider,
) -> None:
    """Staff must be able to tell "we tested this" from "we chased this patient".

    Otherwise a demo leaves fake chases scattered through real history.
    """
    recall = RecallService(db)
    patient = make_patient(organization.id)
    patient.last_annual_visit_date = recall.today_for_org(organization.id) - timedelta(days=400)
    PatientRepository(db).create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()

    body = admin_client.post("/reminders/test", json={}).json()

    assert body["source"] == "TEST"
    assert provider.message_count == 1


# ---------------------------------------------------------------------------------------------
# Demo utilities (SPEC §8, D3)
# ---------------------------------------------------------------------------------------------


def test_a_staff_user_cannot_reach_the_demo_utilities(staff_only_client: TestClient) -> None:
    """One of them emails patients on demand and the other wipes the database."""
    assert staff_only_client.post("/demo/reset").status_code == 403
    assert staff_only_client.post("/internal/jobs/process-reminders/mine").status_code == 403


def test_the_demo_utilities_are_absent_in_production(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 rather than a 403: in production the endpoint does not exist, and answering
    "forbidden" would advertise that it does."""
    from app.core import config

    monkeypatch.setattr(config.settings, "app_env", "production")

    assert admin_client.post("/demo/reset").status_code == 404
    assert admin_client.post("/internal/jobs/process-reminders/mine").status_code == 404


def test_the_performance_strip_counts_every_delivery_state(
    admin_client: TestClient, db: Session, organization: Organization
) -> None:
    body = admin_client.get("/reminders/performance").json()

    for field in ("scheduled", "sent", "delivered", "failed", "total"):
        assert field in body
        assert isinstance(body[field], int)
