"""Health and readiness endpoints.

Two endpoints with deliberately different jobs:

* ``/health``  — is this process alive? Answers without touching anything external, so it stays
  fast and cannot be made to fail by a slow dependency.
* ``/ready``   — can this process actually serve traffic? Checks the database connection.

The distinction matters during a demo: if the app is unreachable, ``/health`` tells you whether
the API is down or whether the database behind it is.

Neither endpoint requires authentication and neither returns any patient data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness check")
def health() -> dict[str, Any]:
    """Return 200 as long as the API process is running."""
    return {
        "status": "ok",
        "service": "clinicrecall-api",
        "environment": settings.app_env,
        # Stated on every response so it is impossible to mistake this deployment for one
        # holding real patient records (SPEC constraint D6).
        "data": "synthetic demo data only",
    }


@router.get("/ready", summary="Readiness check")
def ready() -> dict[str, Any]:
    """Report whether the API can reach its dependencies.

    The database check lands in phase 2, together with the session factory it needs.
    """
    return {"status": "ok", "database": "not yet wired (phase 2)"}
