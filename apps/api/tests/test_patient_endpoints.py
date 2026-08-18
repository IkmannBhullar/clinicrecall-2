"""Patient endpoints over HTTP (SPEC §8).

The list, the drawer, and the four row actions. What matters here beyond "does it work" is that
every route is scoped to the caller's practice — a patients endpoint is the one an attacker would
probe first, and `public_id` values are short enough to be worth guessing at.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_token_claims
from app.core.security import TokenClaims
from app.email.provider import MockEmailProvider, set_email_provider
from app.main import app
from app.models.clinic_settings import ClinicSettings
from app.models.enums import PatientStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.patients import PatientRepository
from app.services.recall import RecallService
from tests.conftest import make_patient


@pytest.fixture
def provider() -> Iterator[MockEmailProvider]:
    mock = MockEmailProvider(delivery_delay_seconds=0.0)
    set_email_provider(mock)
    yield mock
    set_email_provider(None)


@pytest.fixture
def client(db: Session, staff_user: User, provider: MockEmailProvider) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_token_claims] = lambda: TokenClaims(
        subject=staff_user.auth_user_id,
        email=staff_user.email,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def add_patient(db: Session, organization_id, *, first="Ann", last="Smith", days_until_due=-20):  # type: ignore[no-untyped-def]
    recall = RecallService(db)
    today = recall.today_for_org(organization_id)
    patient = make_patient(organization_id, first_name=first, last_name=last)
    patient.last_annual_visit_date = today + timedelta(days=days_until_due) - timedelta(days=365)
    PatientRepository(db).create(organization_id, patient)
    recall.apply_derived_fields(organization_id, patient)
    db.flush()
    return patient


# ---------------------------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------------------------


def test_listing_requires_authentication(db: Session) -> None:
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as anonymous:
            assert anonymous.get("/patients").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_the_list_is_scoped_to_the_callers_practice(
    client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
) -> None:
    add_patient(db, organization.id, first="Ours")
    add_patient(db, other_organization.id, first="Theirs")

    names = [p["first_name"] for p in client.get("/patients").json()["patients"]]

    assert names == ["Ours"]


def test_search_matches_name_and_email(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    add_patient(db, organization.id, first="Sarah", last="Johnson")
    add_patient(db, organization.id, first="Michael", last="Brennan")

    assert client.get("/patients?search=johnson").json()["total"] == 1
    assert client.get("/patients?search=Sarah").json()["total"] == 1
    assert client.get("/patients?search=zzz").json()["total"] == 0


def test_status_filters_combine_as_or(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Selecting two chips shows patients in either — which is what a chip row implies."""
    add_patient(db, organization.id, days_until_due=-40)  # OVERDUE
    add_patient(db, organization.id, days_until_due=-3)  # DUE
    add_patient(db, organization.id, days_until_due=200)  # ACTIVE

    assert client.get("/patients?status=OVERDUE").json()["total"] == 1
    assert client.get("/patients?status=OVERDUE&status=DUE").json()["total"] == 2


def test_pagination_reports_a_usable_position(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    for index in range(7):
        add_patient(db, organization.id, first=f"P{index}")

    page = client.get("/patients?page=2&page_size=3").json()

    assert page["total"] == 7
    assert page["total_pages"] == 3
    assert page["page"] == 2
    assert len(page["patients"]) == 3


def test_the_list_includes_the_last_reminder_column(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §8 names it as a column, so the list endpoint has to supply it."""
    patient = add_patient(db, organization.id)
    row = client.get("/patients").json()["patients"][0]

    assert "last_reminder_at" in row
    assert "last_reminder_status" in row
    assert row["public_id"] == patient.public_id


def test_the_list_never_exposes_a_database_key(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §4.2: only ``public_id`` leaves the server."""
    add_patient(db, organization.id)
    row = client.get("/patients").json()["patients"][0]

    assert "id" not in row
    assert "organization_id" not in row


# ---------------------------------------------------------------------------------------------
# The drawer
# ---------------------------------------------------------------------------------------------


def test_detail_returns_the_reminder_timeline(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    patient = add_patient(db, organization.id)
    body = client.get(f"/patients/{patient.public_id}").json()

    assert body["public_id"] == patient.public_id
    assert isinstance(body["reminders"], list)


def test_detail_contains_no_clinical_fields(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §1 puts clinical documentation out of scope.

    Asserted rather than assumed, because "just one note field" is how that boundary erodes.
    """
    patient = add_patient(db, organization.id)
    body = client.get(f"/patients/{patient.public_id}").json()

    for forbidden in ("diagnosis", "condition", "notes", "visit_reason", "medications"):
        assert forbidden not in body


def test_another_practices_patient_is_not_found(
    client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
) -> None:
    """Indistinguishable from a patient who does not exist, so probing reveals nothing."""
    theirs = add_patient(db, other_organization.id)

    real = client.get(f"/patients/{theirs.public_id}")
    invented = client.get("/patients/ZZZZZZZZZZZZ")

    assert real.status_code == invented.status_code == 404
    assert real.json()["error"]["message"] == invented.json()["error"]["message"]


# ---------------------------------------------------------------------------------------------
# The four actions (SPEC §8)
# ---------------------------------------------------------------------------------------------


def test_send_reminder_adds_to_the_timeline(
    client: TestClient,
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    provider: MockEmailProvider,
) -> None:
    """Step 6 of the demo sequence (SPEC §11)."""
    patient = add_patient(db, organization.id)

    body = client.post(f"/patients/{patient.public_id}/send-reminder").json()

    assert provider.message_count == 1
    assert len(body["patient"]["reminders"]) == 1
    assert body["patient"]["reminders"][0]["source"] == "MANUAL"
    assert patient.first_name in body["message"]


def test_sending_twice_in_quick_succession_is_throttled(
    client: TestClient,
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    provider: MockEmailProvider,
) -> None:
    """SPEC §6.2's per-patient cooldown, protecting the patient's inbox."""
    patient = add_patient(db, organization.id)

    assert client.post(f"/patients/{patient.public_id}/send-reminder").status_code == 200
    second = client.post(f"/patients/{patient.public_id}/send-reminder")

    assert second.status_code == 429
    assert provider.message_count == 1


def test_marking_scheduled_changes_the_status(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Step 8 of the demo sequence (SPEC §11)."""
    patient = add_patient(db, organization.id)
    booking_date = (datetime.now(UTC).date() + timedelta(days=9)).isoformat()

    body = client.post(
        f"/patients/{patient.public_id}/schedule", json={"scheduled_for": booking_date}
    ).json()

    assert body["patient"]["status"] == "SCHEDULED"
    assert body["patient"]["scheduled_for"] == booking_date


def test_an_appointment_cannot_be_booked_in_the_past(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """A booking for last week is a typo, not a booking."""
    patient = add_patient(db, organization.id)
    past = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()

    response = client.post(f"/patients/{patient.public_id}/schedule", json={"scheduled_for": past})

    assert response.status_code == 422


def test_completing_a_visit_rolls_the_cycle_forward(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """The transition the whole product exists to produce."""
    patient = add_patient(db, organization.id, days_until_due=-40)

    body = client.post(f"/patients/{patient.public_id}/complete", json={}).json()

    assert body["patient"]["status"] == "COMPLETED"
    assert body["patient"]["scheduled_for"] is None
    # The confirmation tells staff when the patient is next due, which is the thing they would
    # otherwise have to work out.
    assert "next due" in body["message"]


def test_a_future_visit_date_is_refused(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    patient = add_patient(db, organization.id)
    future = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()

    response = client.post(f"/patients/{patient.public_id}/complete", json={"visit_date": future})

    assert response.status_code == 409
    assert "future" in response.json()["error"]["message"]


def test_pausing_and_resuming_reminders(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    patient = add_patient(db, organization.id)

    paused = client.post(f"/patients/{patient.public_id}/pause").json()
    assert paused["patient"]["status"] == "INACTIVE"
    assert paused["patient"]["reminders_enabled"] is False

    resumed = client.post(f"/patients/{patient.public_id}/resume").json()
    assert resumed["patient"]["reminders_enabled"] is True
    assert resumed["patient"]["status"] != "INACTIVE"


def test_staff_cannot_resume_a_patient_who_unsubscribed(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """The opt-out belongs to the patient.

    If this endpoint could clear it, the unsubscribe link in every reminder would be decorative —
    and a patient who asked to be left alone would start hearing from the practice again.
    """
    patient = add_patient(db, organization.id)
    RecallService(db).record_opt_out(organization.id, patient, opted_out_at=datetime.now(UTC))
    db.flush()

    response = client.post(f"/patients/{patient.public_id}/resume")

    assert response.status_code == 409
    assert "unsubscribed" in response.json()["error"]["message"]
    assert patient.status is PatientStatus.INACTIVE


def test_actions_cannot_reach_another_practices_patient(
    client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
    provider: MockEmailProvider,
) -> None:
    """Every mutating route, not just the read ones.

    A scoping bug on a read leaks data; a scoping bug on `send-reminder` emails another
    practice's patient, which is considerably worse.
    """
    theirs = add_patient(db, other_organization.id)
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    for path, payload in (
        (f"/patients/{theirs.public_id}/send-reminder", None),
        (f"/patients/{theirs.public_id}/schedule", {"scheduled_for": tomorrow}),
        (f"/patients/{theirs.public_id}/complete", {}),
        (f"/patients/{theirs.public_id}/pause", None),
    ):
        response = client.post(path, json=payload) if payload is not None else client.post(path)
        assert response.status_code == 404, path

    assert provider.message_count == 0


# ---------------------------------------------------------------------------------------------
# The rendered message (SPEC §6.4)
# ---------------------------------------------------------------------------------------------


def test_the_stored_message_can_be_retrieved(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """ "Here is exactly what your patient received" — the demo beat, and the honest answer."""
    patient = add_patient(db, organization.id)
    sent = client.post(f"/patients/{patient.public_id}/send-reminder").json()
    reminder_id = sent["patient"]["reminders"][0]["id"]

    body = client.get(f"/patients/{patient.public_id}/reminders/{reminder_id}/message").json()

    assert body["subject"]
    assert "<html" in (body["html"] or "").lower()
    assert patient.first_name in (body["text"] or "")
