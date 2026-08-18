"""FastAPI application entry point.

This module does one job: assemble the application. It wires together configuration, logging,
middleware, error handling, rate limiting, and routers, and it contains no business logic.

The layering rule for the whole backend (SPEC §13):

    routers  ->  services  ->  repositories  ->  models

* ``routers``      speak HTTP. They parse requests, check authorisation, and delegate.
* ``services``     hold the domain rules. They know nothing about HTTP.
* ``repositories`` hold the database queries. Every method takes ``organization_id`` first.
* ``models``       are the SQLAlchemy table definitions.

A router never issues a query, and a service never builds a Response.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    CorrelationIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import register_rate_limiting
from app.core.startup import recompute_all_patient_statuses
from app.routers import (
    activity,
    dashboard,
    health,
    imports,
    jobs,
    me,
    patients,
    reminders,
    unsubscribe,
)
from app.routers import (
    settings as settings_router,
)

# Configured before anything else logs, so the PII redaction filter is in place from the very
# first record (SPEC §9).
configure_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run once on startup, and once on shutdown.

    The status recompute is here because cached statuses go stale simply because time passes
    (SPEC §5.1). Restarting the API between demos, or starting it in the morning after the
    machine slept, would otherwise show yesterday's status badges beside today's dates.

    It never raises — see ``app/core/startup.py`` for why a failed tidy-up must not stop the
    service from booting.
    """
    logger.info("Starting ClinicRecall API (environment=%s)", settings.app_env)
    recompute_all_patient_statuses()
    yield
    # Nothing to tear down: the database engine's connection pool closes itself.


# ---------------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------------

app = FastAPI(
    lifespan=lifespan,
    title="ClinicRecall API",
    description=(
        "Identifies patients due for their annual visit and sends professional reminders. "
        "Demo data only — all patient records are synthetic."
    ),
    version="0.1.0",
    # Interactive docs are a development aid. They are switched off in production so the API
    # surface is not advertised to the internet.
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

# ---------------------------------------------------------------------------------------------
# Middleware
#
# ORDER IS SIGNIFICANT. Starlette applies middleware in reverse registration order on the way in,
# so the last one registered is the outermost and runs first. The registrations below are
# therefore written in reverse of the order they execute:
#
#     request  ->  CorrelationId  ->  SecurityHeaders  ->  RequestSizeLimit  ->  CORS  ->  route
#
# CorrelationId must be outermost so that everything inside it — including a rejection from the
# size limiter and any error handler — has an identifier to log and to return.
# ---------------------------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    # Exactly one origin, from configuration (SPEC §9). Never a wildcard: with
    # allow_credentials=True a wildcard is both rejected by browsers and, if it were honoured,
    # would let any site on the internet make authenticated requests on a user's behalf.
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    # Lets the browser read the correlation ID off a response, so the frontend can surface it in
    # an error message.
    expose_headers=["X-Correlation-ID"],
)

app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)

# ---------------------------------------------------------------------------------------------
# Cross-cutting handlers
# ---------------------------------------------------------------------------------------------

register_error_handlers(app)
register_rate_limiting(app)

# ---------------------------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(me.router)
app.include_router(dashboard.router)
app.include_router(patients.router)
app.include_router(activity.router)
app.include_router(reminders.router)
app.include_router(settings_router.router)
app.include_router(imports.router)
app.include_router(jobs.router)
app.include_router(unsubscribe.router)
