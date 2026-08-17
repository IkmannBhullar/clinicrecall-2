"""The CSV import endpoints, over HTTP (SPEC §7).

The service-level rules are covered in ``test_csv_import.py``. What is checked here is the part a
browser actually touches: that a multipart upload is parsed, that preview genuinely writes
nothing, that the commit is one transaction, and that the error report arrives as a downloadable
file rather than as JSON.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_token_claims
from app.core.security import TokenClaims
from app.main import app
from app.models.clinic_settings import ClinicSettings
from app.models.organization import Organization
from app.models.user import User
from app.repositories.patients import PatientRepository

MESSY_SAMPLE = Path(__file__).resolve().parents[3] / "docs" / "samples" / "patients-messy.csv"


@pytest.fixture
def client(db: Session, staff_user: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_token_claims] = lambda: TokenClaims(
        subject=staff_user.auth_user_id,
        email=staff_user.email,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    # raise_server_exceptions=False so an unhandled exception becomes a 500 *response*, which
    # is what a browser would receive. The default re-raises it into the test instead, which
    # makes it impossible to assert on the error the user actually sees.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def upload(name: str = "patients.csv", *rows: str) -> dict[str, tuple[str, bytes, str]]:
    header = "first_name,last_name,email,phone,last_annual_visit_date,external_id"
    content = "\n".join([header, *rows]) + "\n"
    return {"file": (name, content.encode(), "text/csv")}


def days_ago(count: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=count)).isoformat()


# ---------------------------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------------------------


def test_import_requires_authentication(db: Session) -> None:
    """A patient list is the most sensitive thing a practice uploads."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as anonymous:
            response = anonymous.post(
                "/patients/import/preview",
                files=upload("p.csv", f"Ann,Smith,a@example.com,,{days_ago(100)},"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


# ---------------------------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------------------------


def test_preview_returns_the_demo_numbers_over_http(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §7.2's four numbers, through the endpoint the UI actually calls."""
    with MESSY_SAMPLE.open("rb") as handle:
        response = client.post(
            "/patients/import/preview",
            files={"file": ("patients-messy.csv", handle.read(), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 327
    assert body["valid_rows"] == 320
    assert body["missing_required"] == 5
    assert body["invalid_email"] == 2


def test_preview_writes_nothing(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """The whole point of a preview. A practice must be able to look before committing."""
    before = PatientRepository(db).count(organization.id)

    client.post(
        "/patients/import/preview",
        files=upload("p.csv", f"Ann,Smith,ann@example.com,,{days_ago(100)},"),
    )

    assert PatientRepository(db).count(organization.id) == before


def test_preview_separates_new_from_updates(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §7.1: "X new, Y updates" — distinctly."""
    rows = (f"Ann,Smith,ann@example.com,,{days_ago(200)},MRN-1",)
    client.post("/patients/import", files=upload("p.csv", *rows))

    body = client.post(
        "/patients/import/preview",
        files=upload(
            "p.csv",
            f"Ann,Smith,ann@example.com,,{days_ago(100)},MRN-1",
            f"Bob,Jones,bob@example.com,,{days_ago(100)},MRN-2",
        ),
    ).json()

    assert body["update_count"] == 1
    assert body["new_count"] == 1


def test_the_problem_list_is_capped_but_the_counts_are_not(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """A file with thousands of bad rows must not produce a multi-megabyte JSON response.

    The counts stay exact — only the per-row detail is capped, and the error-report download
    carries every one of them.
    """
    from app.routers.imports import MAX_PROBLEMS_IN_RESPONSE

    broken = [",,,,," for _ in range(MAX_PROBLEMS_IN_RESPONSE + 50)]
    body = client.post("/patients/import/preview", files=upload("p.csv", *broken)).json()

    assert body["missing_required"] == MAX_PROBLEMS_IN_RESPONSE + 50
    assert len(body["problems"]) == MAX_PROBLEMS_IN_RESPONSE


# ---------------------------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------------------------


def test_importing_creates_patients(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    response = client.post(
        "/patients/import",
        files=upload(
            "p.csv",
            f"Ann,Smith,ann@example.com,555-0100,{days_ago(100)},MRN-1",
            f"Bob,Jones,bob@example.com,,{days_ago(400)},MRN-2",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert PatientRepository(db).count(organization.id) == 2


def test_bad_rows_are_skipped_and_counted_not_dropped(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """SPEC §7.1: "Never silently drop a row."

    The good rows import, the bad row is reported, and the totals account for both.
    """
    body = client.post(
        "/patients/import",
        files=upload(
            "p.csv",
            f"Ann,Smith,ann@example.com,,{days_ago(100)},",
            f"Bad,Row,not-an-email,,{days_ago(100)},",
            f"Bob,Jones,bob@example.com,,{days_ago(100)},",
        ),
    ).json()

    assert body["total_rows"] == 3
    assert body["created"] == 2
    assert body["skipped"] == 1


def test_importing_the_messy_sample_end_to_end(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Step 10 of the demo sequence (SPEC §11), through the real endpoint."""
    with MESSY_SAMPLE.open("rb") as handle:
        response = client.post(
            "/patients/import",
            files={"file": ("patients-messy.csv", handle.read(), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 320
    assert body["skipped"] == 7
    assert PatientRepository(db).count(organization.id) == 320


def test_a_non_csv_upload_is_refused_with_advice(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Someone will upload the .xlsx, because that is what their export tool produced.

    The message tells them what to do rather than merely refusing.
    """
    response = client.post(
        "/patients/import/preview",
        files={"file": ("patients.xlsx", b"PK\x03\x04binary", "application/vnd.ms-excel")},
    )

    assert response.status_code == 422
    assert "Save As" in response.json()["error"]["message"]


def test_a_file_with_wrong_columns_is_refused(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    response = client.post(
        "/patients/import/preview",
        files={"file": ("p.csv", b"name,dob\nAnn Smith,1980-01-01\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "email" in response.json()["error"]["message"]


def test_the_import_is_scoped_to_the_callers_practice(
    client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
) -> None:
    """The organization comes from the token, never from the request (SPEC §3.2).

    An attempt to name a different practice in the form data must have no effect.
    """
    client.post(
        "/patients/import",
        files=upload("p.csv", f"Ann,Smith,ann@example.com,,{days_ago(100)},"),
        data={"organization_id": str(other_organization.id)},
    )

    assert PatientRepository(db).count(organization.id) == 1
    assert PatientRepository(db).count(other_organization.id) == 0


# ---------------------------------------------------------------------------------------------
# The error report (SPEC §7.1)
# ---------------------------------------------------------------------------------------------


def test_the_error_report_downloads_as_a_csv(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    with MESSY_SAMPLE.open("rb") as handle:
        response = client.post(
            "/patients/import/errors",
            files={"file": ("patients-messy.csv", handle.read(), "text/csv")},
        )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "import-errors.csv" in response.headers["content-disposition"]


def test_the_error_report_contains_every_problem_not_the_capped_list(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """This is the file someone sits with next to their export.

    A truncated one would send them back for another round, which is exactly the friction the
    report exists to remove.
    """
    from app.routers.imports import MAX_PROBLEMS_IN_RESPONSE

    broken = [",,,,," for _ in range(MAX_PROBLEMS_IN_RESPONSE + 50)]
    report = client.post("/patients/import/errors", files=upload("p.csv", *broken)).text

    # Header plus one line per problem.
    assert len(report.strip().splitlines()) == MAX_PROBLEMS_IN_RESPONSE + 50 + 1


def test_the_error_report_names_rows_the_way_a_spreadsheet_does(
    client: TestClient, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Row 1 is the header, so the first data row is row 2 — matching what Excel shows the
    practice when they open the file to fix it."""
    report = client.post(
        "/patients/import/errors",
        files=upload("p.csv", f"Ann,Smith,not-an-email,,{days_ago(100)},"),
    ).text

    lines = report.strip().splitlines()
    assert lines[1].startswith("2,")


def test_generating_the_report_writes_nothing(
    client: TestClient, db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Downloading an error report must not import the good rows as a side effect."""
    client.post(
        "/patients/import/errors",
        files=upload(
            "p.csv",
            f"Ann,Smith,ann@example.com,,{days_ago(100)},",
            f"Bad,Row,nope,,{days_ago(100)},",
        ),
    )

    assert PatientRepository(db).count(organization.id) == 0


# ---------------------------------------------------------------------------------------------
# Transactional behaviour (SPEC §7.1)
# ---------------------------------------------------------------------------------------------
#
# "Import runs in a single transaction with a summary activity event; partial failure does not
# leave half-imported state."
#
# This is deliberately NOT tested by making an endpoint raise and asserting the rows vanished.
# The test client overrides `get_db` with the test's own session, which bypasses the try/except
# in `get_db` where the rollback actually happens — so such a test would be checking the
# override, not the application.
#
# Instead the guarantee is tested where it lives, in two independent halves:
#   1. `commit()` does not commit — the caller owns the transaction boundary.
#   2. `get_db` rolls back when the request raises.
# Together those are the property; separately, each is checkable without fighting the harness.


def test_the_import_service_does_not_commit_by_itself(
    db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> None:
    """Half the guarantee: writes are staged, not committed.

    Verified from a *separate* database connection, which by definition cannot see another
    transaction's uncommitted work. If `commit()` were issuing its own COMMIT, those rows would
    be visible here.
    """
    from sqlalchemy import text as sql_text

    from app.core.database import engine
    from app.services.csv_import import CsvImportService

    service = CsvImportService(db)
    rows = [f"P{index},Test,p{index}@example.com,,{days_ago(100)}," for index in range(10)]
    preview = service.build_preview(
        organization.id,
        io.BytesIO(
            (
                "first_name,last_name,email,phone,last_annual_visit_date,external_id\n"
                + "\n".join(rows)
                + "\n"
            ).encode()
        ),
    )
    service.commit(organization.id, preview)
    db.flush()

    with engine.connect() as other_connection:
        visible = other_connection.execute(
            sql_text("SELECT count(*) FROM patients WHERE organization_id = :org"),
            {"org": str(organization.id)},
        ).scalar_one()

    assert visible == 0, "the import committed on its own; a failure could not be rolled back"


def test_get_db_rolls_back_when_the_request_raises() -> None:
    """The other half: the request-scoped session discards its work on any exception.

    This is what turns "the service stages writes" into "a failed import leaves nothing behind".
    """
    from unittest.mock import MagicMock, patch

    from app.core.database import get_db

    fake_session = MagicMock()

    with patch("app.core.database.SessionLocal", return_value=fake_session):
        generator = get_db()
        next(generator)

        with pytest.raises(RuntimeError):
            generator.throw(RuntimeError("something failed mid-request"))

    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()
    fake_session.commit.assert_not_called()
