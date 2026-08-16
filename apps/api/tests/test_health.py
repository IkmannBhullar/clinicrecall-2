"""Smoke tests for the system endpoints.

These exist mostly to prove the application actually boots. A surprising share of "the demo is
broken" moments are import errors or a mistyped setting, and this catches those in under a
second rather than on stage.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "clinicrecall-api"


def test_health_declares_synthetic_data() -> None:
    """SPEC constraint D6: it must never be ambiguous that this holds synthetic data."""
    body = client.get("/health").json()

    assert "synthetic" in body["data"].lower()


def test_ready_reports_the_database_is_reachable() -> None:
    """Requires the local Supabase stack to be running — see tests/conftest.py."""
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_no_hipaa_compliance_claim_in_api_description() -> None:
    """SPEC constraint D6: nothing may claim HIPAA compliance.

    Checked as a test rather than trusted to review, because this is a legal-exposure issue and
    marketing-flavoured copy has a way of drifting back in.
    """
    schema = client.get("/openapi.json").json()
    description = (schema["info"].get("description") or "").lower()

    assert "hipaa" not in description
