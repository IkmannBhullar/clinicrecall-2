"""Configuration and endpoints that only matter once the app is deployed somewhere public.

The public demo is a production deployment that deliberately keeps its demo utilities switched
on, and exposes a token-guarded endpoint that deletes every row on a schedule. Both of those are
reasonable for a demo and alarming anywhere else, so the switches that separate the two cases are
tested rather than assumed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.database import get_db
from app.main import app


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ------------------------------------------------------------------------------- CORS allowlist


def test_a_single_origin_is_the_whole_allowlist() -> None:
    config = Settings(web_origin="https://clinicrecall.vercel.app")

    assert config.allowed_origins == ["https://clinicrecall.vercel.app"]


def test_extra_origins_are_appended_in_order() -> None:
    """Vercel gives every preview deployment its own hostname, so one origin is not enough."""
    config = Settings(
        web_origin="https://clinicrecall.vercel.app",
        extra_web_origins="https://preview-a.vercel.app, https://preview-b.vercel.app",
    )

    assert config.allowed_origins == [
        "https://clinicrecall.vercel.app",
        "https://preview-a.vercel.app",
        "https://preview-b.vercel.app",
    ]


def test_blank_entries_and_trailing_slashes_do_not_produce_junk_origins() -> None:
    """A trailing slash makes an origin that never matches, which reads as a mystery CORS bug."""
    config = Settings(
        web_origin="https://clinicrecall.vercel.app",
        extra_web_origins="  , https://preview.vercel.app/ ,,",
    )

    assert config.allowed_origins == [
        "https://clinicrecall.vercel.app",
        "https://preview.vercel.app",
    ]


def test_the_canonical_origin_is_never_duplicated() -> None:
    config = Settings(
        web_origin="https://clinicrecall.vercel.app",
        extra_web_origins="https://clinicrecall.vercel.app",
    )

    assert config.allowed_origins == ["https://clinicrecall.vercel.app"]


def test_a_wildcard_is_dropped_rather_than_honoured() -> None:
    """With allow_credentials=True a wildcard is rejected by browsers, so passing one through
    would not widen access — it would silently break every cross-origin request instead."""
    config = Settings(web_origin="https://clinicrecall.vercel.app", extra_web_origins="*")

    assert config.allowed_origins == ["https://clinicrecall.vercel.app"]


# --------------------------------------------------------------------------- Demo utility gating


def test_demo_utilities_are_on_outside_production() -> None:
    assert Settings(app_env="development").demo_utilities_enabled is True


def test_demo_utilities_are_off_in_production_by_default() -> None:
    assert Settings(app_env="production").demo_utilities_enabled is False


def test_demo_mode_re_enables_them_in_production() -> None:
    """The public demo is genuinely production — production logging, error handling, and security
    headers — and still wants the reset. That is what the explicit flag buys."""
    assert Settings(app_env="production", demo_mode=True).demo_utilities_enabled is True


def test_demo_mode_is_off_unless_asked_for() -> None:
    """A deployment must never acquire a row-deleting endpoint by accident."""
    assert Settings().demo_mode is False


# ------------------------------------------------------------------------ The scheduled reset


def test_the_reset_rejects_a_missing_token(client: TestClient) -> None:
    assert client.post("/internal/jobs/reset-demo").status_code == 401


def test_the_reset_rejects_a_wrong_token(client: TestClient) -> None:
    response = client.post("/internal/jobs/reset-demo", headers={"X-Job-Token": "not-the-token"})

    assert response.status_code == 401


def test_the_rejection_does_not_reveal_which_way_it_was_wrong(client: TestClient) -> None:
    absent = client.post("/internal/jobs/reset-demo").json()
    wrong = client.post("/internal/jobs/reset-demo", headers={"X-Job-Token": "nope"}).json()

    assert absent["error"]["code"] == wrong["error"]["code"]
    assert absent["error"]["message"] == wrong["error"]["message"]


def test_a_valid_token_is_still_refused_when_demo_utilities_are_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is not sufficient on its own. A deployment that is not a demo must not expose an
    endpoint that deletes every row, however well-guarded."""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "demo_mode", False)

    response = client.post("/internal/jobs/reset-demo", headers={"X-Job-Token": settings.job_token})

    # 404 rather than 403 — a switched-off endpoint should not confirm that it exists.
    assert response.status_code == 404
