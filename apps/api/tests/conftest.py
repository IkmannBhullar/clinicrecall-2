"""Shared pytest fixtures.

**The isolation strategy.** Every database test runs inside a transaction that is rolled back
when the test ends. Nothing a test writes ever reaches the next test, and nothing it writes
survives the run — so the suite can be executed against the same local database you are
developing in without wrecking your demo data.

This is much faster than recreating the schema per test, and unlike a "delete everything
afterwards" approach it cannot leave debris behind when a test fails partway through.

**Why the tests need a real Postgres.** SQLite would be quicker to set up but would test a
different product: this schema relies on partial indexes, functional indexes on ``lower(email)``,
JSONB, and native enum types, and the reminder idempotency guarantee is a Postgres unique-index
behaviour. Testing against a database that fakes those would prove nothing about the one we ship.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.rate_limit import limiter
from app.core.security import reset_jwks_cache
from app.models import (
    ClinicSettings,
    Organization,
    Patient,
    PatientStatus,
    User,
    UserRole,
)

# ---------------------------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def require_database() -> None:
    """Fail the run immediately, with a useful message, if the database is unreachable.

    Without this the suite produces dozens of near-identical connection errors and the actual
    problem — "you forgot to start Docker" — is buried. One clear message at the top is worth
    considerably more.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.exit(
            "\n\nCannot reach the database.\n\n"
            "  The test suite needs the local Supabase stack running. Start it with:\n\n"
            "      make supabase-start\n\n"
            "  If that fails, check Docker is running (`docker info`).\n\n"
            f"  Underlying error: {exc}\n",
            returncode=1,
        )


@pytest.fixture(autouse=True)
def isolate_jwks_cache() -> Generator[None, None, None]:
    """Give every test a clean JWKS cache.

    ``app.core.security`` keeps the cache in a module-level global, which is right for the
    application — one process, one key set — and a hazard in a test suite, because a stub
    installed by one test is visible to every test that follows.

    That is not hypothetical: it caused an intermittent failure in the Supabase integration
    tests, which reached for a real signing key inside a stub cache that had never heard of it.
    Resetting on both sides makes the ordering irrelevant.
    """
    reset_jwks_cache()
    yield
    reset_jwks_cache()


@pytest.fixture(autouse=True)
def reset_rate_limits() -> Generator[None, None, None]:
    """Give every test a fresh rate-limit budget.

    slowapi keeps its counters in process memory, keyed by client address — and every test shares
    the address "testclient". Without this, the sixteenth import test in a file starts failing
    with 429 because the fifteen before it used up the 5-per-minute budget.

    That failure is particularly misleading: it appears in whichever test happens to run
    sixteenth, moves when tests are reordered, and looks like a bug in the endpoint under test
    rather than in the harness. Resetting between tests removes the coupling entirely, and the
    limits themselves are still tested explicitly where they matter.
    """
    limiter.reset()
    yield


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """A database session whose work is discarded when the test finishes.

    The mechanism: open a connection, begin a transaction on it, and bind the session to that
    connection. The session's own commits become nested savepoint releases rather than real
    commits, so the outer transaction still owns everything. Rolling it back at the end erases
    the whole test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------------------------
# Domain fixtures
#
# Two organizations, always. Almost every tenancy bug looks fine with one tenant in the database
# — a missing WHERE clause returns exactly the right rows when there is nothing else to return.
# Having a second practice present by default means those bugs fail a test instead.
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def organization(db: Session) -> Organization:
    """The organization under test — "Green Valley Family Clinic"."""
    org = Organization(
        name="Green Valley Family Clinic", slug=f"green-valley-{uuid.uuid4().hex[:8]}"
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture
def other_organization(db: Session) -> Organization:
    """A second, unrelated practice.

    Its records must never appear in any query scoped to ``organization``. If they do, that is
    the single worst bug this product could ship, and the tests in ``test_org_isolation.py``
    exist to make sure it cannot.
    """
    org = Organization(name="Riverside Medical Group", slug=f"riverside-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


@pytest.fixture
def clinic_settings(db: Session, organization: Organization) -> ClinicSettings:
    """Settings for the organization under test.

    Present in most tests because ``RecallService`` reads ``timezone`` and
    ``annual_interval_months`` from here on every status computation.
    """
    settings = ClinicSettings(
        organization_id=organization.id,
        clinic_name="Green Valley Family Clinic",
        timezone="America/Los_Angeles",
        annual_interval_months=12,
    )
    db.add(settings)
    db.flush()
    return settings


@pytest.fixture
def staff_user(db: Session, organization: Organization) -> User:
    """An admin staff member at the organization under test."""
    user = User(
        organization_id=organization.id,
        auth_user_id=uuid.uuid4(),
        first_name="Alex",
        last_name="Morgan",
        email=f"alex.morgan+{uuid.uuid4().hex[:8]}@example.com",
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------------------------


def make_patient(
    organization_id: uuid.UUID,
    *,
    first_name: str = "Test",
    last_name: str = "Patient",
    email: str | None = None,
    external_id: str | None = None,
    days_until_due: int = 90,
    status: PatientStatus = PatientStatus.ACTIVE,
) -> Patient:
    """Build (but do not save) a patient with sensible defaults.

    ``days_until_due`` is relative to today rather than an absolute date, for the same reason the
    seed is (SPEC D1): a test written against a fixed calendar date starts failing on its own
    the moment that date drifts into the past.

    Emails default to a unique value because ``(organization_id, lower(email))`` is a unique
    index — two patients built with the same default would collide in a way that has nothing to
    do with what the test is checking.
    """
    today = datetime.now(UTC).date()
    unique = uuid.uuid4().hex[:8]

    return Patient(
        organization_id=organization_id,
        first_name=first_name,
        last_name=last_name,
        email=email or f"{first_name.lower()}.{last_name.lower()}+{unique}@example.com",
        external_id=external_id,
        # Wind the last visit back a year from the due date, so the two dates are consistent with
        # each other rather than arbitrary.
        last_annual_visit_date=today + timedelta(days=days_until_due) - timedelta(days=365),
        next_annual_due_date=today + timedelta(days=days_until_due),
        status=status,
    )


@pytest.fixture
def patient_factory():  # type: ignore[no-untyped-def]
    """Expose ``make_patient`` as a fixture for tests that prefer injection over import."""
    return make_patient
