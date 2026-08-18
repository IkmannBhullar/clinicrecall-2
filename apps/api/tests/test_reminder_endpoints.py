"""The reminder job endpoint and the unsubscribe page, over HTTP (SPEC §6.3, §6.5).

Two routes with unusual authentication, for good reasons:

* ``POST /internal/jobs/process-reminders`` is called by a scheduler, which has no user session,
  so it is authenticated by a shared secret header.
* ``GET /unsubscribe/{token}`` is opened by a patient from an email, who has no account at all,
  so it is authenticated by the signature in the URL.

Both are therefore reachable without signing in, which makes them the two most exposed surfaces
in the application and the two most worth testing carefully.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tokens import make_unsubscribe_token
from app.email.provider import MockEmailProvider, set_email_provider
from app.main import app
from app.models.clinic_settings import ClinicSettings
from app.models.enums import PatientStatus
from app.models.organization import Organization
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


@pytest.fixture
def client(db: Session, provider: MockEmailProvider) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def due_patient(db: Session, organization: Organization, clinic_settings: ClinicSettings):  # type: ignore[no-untyped-def]
    """One patient due today, in a practice with the four standard rules."""
    ReminderRuleRepository(db).create_default_rules(organization.id)
    recall = RecallService(db)
    today = recall.today_for_org(organization.id)

    patient = make_patient(organization.id, first_name="Jennifer", last_name="Tran")
    patient.last_annual_visit_date = today - timedelta(days=365)
    PatientRepository(db).create(organization.id, patient)
    recall.apply_derived_fields(organization.id, patient)
    db.flush()
    return patient


# ---------------------------------------------------------------------------------------------
# The job endpoint (SPEC §6.3)
# ---------------------------------------------------------------------------------------------


def test_the_job_requires_a_token(client: TestClient) -> None:
    response = client.post("/internal/jobs/process-reminders")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_the_job_rejects_a_wrong_token(client: TestClient) -> None:
    response = client.post(
        "/internal/jobs/process-reminders", headers={"X-Job-Token": "not-the-token"}
    )

    assert response.status_code == 401


def test_the_rejection_does_not_say_which_way_it_was_wrong(client: TestClient) -> None:
    """Absent and incorrect must be indistinguishable, or the endpoint helps an attacker
    work out whether they have found a real header name."""
    absent = client.post("/internal/jobs/process-reminders").json()
    wrong = client.post("/internal/jobs/process-reminders", headers={"X-Job-Token": "nope"}).json()

    assert absent["error"]["code"] == wrong["error"]["code"]
    assert absent["error"]["message"] == wrong["error"]["message"]


def test_the_job_runs_with_the_correct_token(
    client: TestClient,
    due_patient,
    provider: MockEmailProvider,  # type: ignore[no-untyped-def]
) -> None:
    response = client.post(
        "/internal/jobs/process-reminders", headers={"X-Job-Token": settings.job_token}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["sent"] == 1
    assert provider.message_count == 1


def test_the_job_returns_the_structured_summary_the_spec_requires(
    client: TestClient,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """SPEC §6.3 names these fields exactly. The frontend and the demo both read them."""
    body = client.post(
        "/internal/jobs/process-reminders", headers={"X-Job-Token": settings.job_token}
    ).json()

    for field in ("evaluated", "eligible", "created", "skipped_duplicate", "sent", "failed"):
        assert field in body, f"job summary is missing {field}"
        assert isinstance(body[field], int)


def test_running_the_job_twice_over_http_sends_once(
    client: TestClient,
    db: Session,
    organization: Organization,
    due_patient,
    provider: MockEmailProvider,  # type: ignore[no-untyped-def]
) -> None:
    """The idempotency guarantee, end to end rather than at the service layer.

    A scheduler that fires twice, or an operator rerunning after a timeout, must not produce a
    second email.

    Asserted against this test's own organization rather than the response totals. This endpoint
    processes **every** practice, and the database also holds the committed demo seed — so
    ``skipped_duplicate`` in the response is a sum across tenants, and pinning it to an exact
    number would make the test depend on how many patients the seed happens to contain.
    """
    from app.models.reminder_event import ReminderEvent

    headers = {"X-Job-Token": settings.job_token}

    def our_event_count() -> int:
        return (
            db.query(ReminderEvent).filter(ReminderEvent.organization_id == organization.id).count()
        )

    first = client.post("/internal/jobs/process-reminders", headers=headers).json()
    after_first = our_event_count()

    second = client.post("/internal/jobs/process-reminders", headers=headers).json()
    after_second = our_event_count()

    # Our patient was reminded once, and the second run added nothing.
    assert after_first == 1
    assert after_second == 1
    assert provider.message_count == 1

    # Globally, the first run did work and the second sent nothing anywhere.
    assert first["sent"] >= 1
    assert second["sent"] == 0
    assert second["skipped_duplicate"] >= 1


def test_the_admin_utility_endpoint_requires_authentication(client: TestClient) -> None:
    """The Settings button is admin-only, and anonymous callers get nothing."""
    response = client.post("/internal/jobs/process-reminders/mine")

    assert response.status_code == 401


# ---------------------------------------------------------------------------------------------
# The unsubscribe page (SPEC §6.5)
# ---------------------------------------------------------------------------------------------


def test_a_valid_link_opts_the_patient_out(
    client: TestClient,
    db: Session,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    token = make_unsubscribe_token(due_patient.public_id)

    response = client.get(f"/unsubscribe/{token}")

    assert response.status_code == 200
    assert "unsubscribed" in response.text.lower()

    db.refresh(due_patient)
    assert due_patient.opted_out_at is not None
    assert due_patient.status is PatientStatus.INACTIVE


def test_an_opted_out_patient_stops_receiving_reminders(
    client: TestClient,
    db: Session,
    due_patient,
    provider: MockEmailProvider,  # type: ignore[no-untyped-def]
) -> None:
    """The point of the whole feature, checked end to end.

    An opt-out that did not actually stop the mail would be worse than having none — it would be
    a promise the product breaks.
    """
    client.get(f"/unsubscribe/{make_unsubscribe_token(due_patient.public_id)}")

    response = client.post(
        "/internal/jobs/process-reminders", headers={"X-Job-Token": settings.job_token}
    )

    assert response.json()["sent"] == 0
    assert provider.message_count == 0


def test_clicking_the_link_twice_is_not_an_error(
    client: TestClient,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """Someone unsure whether it worked will click again, and must see the same confirmation."""
    token = make_unsubscribe_token(due_patient.public_id)

    first = client.get(f"/unsubscribe/{token}")
    second = client.get(f"/unsubscribe/{token}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "unsubscribed" in second.text.lower()


def test_a_forged_link_is_refused(
    client: TestClient,
    db: Session,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """Without the signature check, anyone could opt out a clinic's entire recall list."""
    response = client.get(f"/unsubscribe/{due_patient.public_id}.forgedsignature")

    assert response.status_code == 400
    assert "not valid" in response.text.lower()

    db.refresh(due_patient)
    assert due_patient.opted_out_at is None


def test_an_unsigned_identifier_is_refused(
    client: TestClient,
    db: Session,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """The naive URL shape, explicitly rejected."""
    response = client.get(f"/unsubscribe/{due_patient.public_id}")

    assert response.status_code == 400
    db.refresh(due_patient)
    assert due_patient.opted_out_at is None


def test_a_well_signed_token_for_an_unknown_patient_looks_the_same(client: TestClient) -> None:
    """ "No such patient" and "bad signature" must be indistinguishable.

    Otherwise the endpoint becomes an oracle for discovering which identifiers are real.
    """
    response = client.get(f"/unsubscribe/{make_unsubscribe_token('NOSUCHPATIENT')}")

    assert response.status_code == 400
    assert "not valid" in response.text.lower()


def test_the_unsubscribe_page_loads_nothing_from_the_network(
    client: TestClient,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """It is opened from an email client, often on a poor mobile connection.

    Every external request is one more thing that can fail in front of someone trying to withdraw
    consent — and SPEC constraint D2 forbids it regardless.
    """
    html = client.get(f"/unsubscribe/{make_unsubscribe_token(due_patient.public_id)}").text

    for pattern in ("<script", "<img", "@import", "https://fonts.", "cdn."):
        assert pattern not in html.lower()


def test_the_unsubscribe_page_says_the_data_is_synthetic(
    client: TestClient,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """SPEC constraint D6 — including on the one page a patient might reach."""
    html = client.get(f"/unsubscribe/{make_unsubscribe_token(due_patient.public_id)}").text

    assert "synthetic" in html.lower()


def test_the_opt_out_is_recorded_as_an_activity_event(
    client: TestClient,
    db: Session,
    organization: Organization,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """SPEC §6.5 requires it, and a withdrawal of consent is exactly the kind of thing that must
    be traceable afterwards."""
    from app.models.enums import ActivityEventType
    from app.repositories.activity_events import ActivityEventRepository

    client.get(f"/unsubscribe/{make_unsubscribe_token(due_patient.public_id)}")

    events = ActivityEventRepository(db).list_for_patient(organization.id, due_patient.id)
    opt_outs = [e for e in events if e.type is ActivityEventType.PATIENT_OPTED_OUT]

    assert len(opt_outs) == 1
    # The patient did this, not a member of staff.
    assert opt_outs[0].actor_user_id is None
    # Initials only, never a name or an address (SPEC §9).
    assert "Jennifer" not in str(opt_outs[0].payload)
    assert opt_outs[0].payload["patient_initials"] == "JT"


# ---------------------------------------------------------------------------------------------
# Delivery transitions (SPEC §6.4)
# ---------------------------------------------------------------------------------------------


def test_sent_becomes_delivered_after_the_delay(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    due_patient,  # type: ignore[no-untyped-def]
) -> None:
    """The mock reports a delay so the UI shows a real SENT → DELIVERED transition.

    A badge that appears already in its final state makes the demo look pre-baked; watching it
    change is what makes it look alive.
    """
    from app.models.enums import ReminderEventStatus
    from app.models.reminder_event import ReminderEvent
    from app.services.reminders import ReminderService

    slow_provider = MockEmailProvider(delivery_delay_seconds=2.0)
    service = ReminderService(db, provider=slow_provider)

    service.process_reminders(organization.id)
    db.flush()

    # Scoped to this test's organization: the database also holds the committed demo seed,
    # and an unscoped query would pick up its events too.
    event = db.query(ReminderEvent).filter(ReminderEvent.organization_id == organization.id).one()
    assert event.status is ReminderEventStatus.SENT
    assert event.delivered_at is None

    # Nothing has changed yet — the delay has not elapsed.
    assert service.confirm_deliveries(organization.id) == 0
    assert event.status is ReminderEventStatus.SENT

    # Ask what happens once it has, rather than sleeping for two seconds.
    later = datetime.now(UTC) + timedelta(seconds=5)
    assert service.confirm_deliveries(organization.id, now=later) == 1

    assert event.status is ReminderEventStatus.DELIVERED
    assert event.delivered_at is not None
