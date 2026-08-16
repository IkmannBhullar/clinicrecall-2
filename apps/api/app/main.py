"""FastAPI application entry point.

This module does one job: assemble the application. It wires together configuration, middleware,
and routers, and it contains no business logic of its own.

The layering rule for the whole backend (SPEC section 13):

    routers  ->  services  ->  repositories  ->  models

* ``routers``      speak HTTP. They parse requests, check authorisation, and delegate.
* ``services``     hold the domain rules. They know nothing about HTTP.
* ``repositories`` hold the database queries. Every method takes ``organization_id`` first.
* ``models``       are the SQLAlchemy table definitions.

A router never issues a query, and a service never builds a Response.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import settings
from app.routers import health

# ---------------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------------

app = FastAPI(
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
# Routers
# ---------------------------------------------------------------------------------------------
# Middleware (CORS, security headers, rate limiting, error envelope) is added in phase 4, where
# it can be built and tested alongside authentication rather than half-configured here.

app.include_router(health.router)
