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

from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.core.database import check_database_connection

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
def ready(response: Response) -> dict[str, Any]:
    """Report whether the API can actually serve traffic.

    Returns 503 rather than 200 when the database is unreachable. That distinction is the whole
    point of having a second endpoint: if the app is not working, ``/health`` says the process is
    alive and this says the database behind it is not — which is the first thing worth knowing
    when something is wrong five minutes before a demo.
    """
    database_ok = check_database_connection()

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if database_ok else "degraded",
        "database": "connected" if database_ok else "unreachable",
    }
