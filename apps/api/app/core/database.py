"""Database engine, sessions, and the FastAPI dependency that hands one to a request.

**Why synchronous SQLAlchemy.** FastAPI is often written with ``async def`` endpoints and an
async driver. This project uses ordinary synchronous SQLAlchemy, and endpoints that touch the
database are plain ``def`` functions, which FastAPI runs in a thread pool.

That is a deliberate application of SPEC §13 ("prefer boring, readable code over clever
abstraction"). Async SQLAlchemy introduces a family of failure modes — forgotten ``await``s,
greenlet errors when a lazy relationship loads outside the async context, session lifetimes that
behave differently under load — in exchange for concurrency this application does not need. The
largest single practice has tens of thousands of patients; the demo has fifty-five.

**Session lifetime.** One session per request, opened by ``get_db`` and closed when the response
is finished. A request that raises rolls back. This gives every endpoint a single transaction by
default, which is what SPEC §7.1 needs for "import runs in a single transaction; partial failure
does not leave half-imported state".
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ---------------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------------

engine: Engine = create_engine(
    settings.database_url,
    # Verify a pooled connection is still alive before handing it out. Without this, a connection
    # that died while the laptop was asleep is handed to the next request and fails — which is
    # precisely the situation a demo machine is in when it is opened in a clinic.
    pool_pre_ping=True,
    # Small pool: this is a single-tenant demo deployment, not a service under load.
    pool_size=5,
    max_overflow=5,
    # Recycle connections before Postgres' own idle timeout can close them underneath us.
    pool_recycle=1800,
    # Echo every statement when running at DEBUG. Very useful while building; far too noisy
    # otherwise, and it would print patient data to the console.
    echo=settings.log_level == "DEBUG",
)

# ---------------------------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    # Keep attributes usable after commit(). With the default (True), reading any attribute of an
    # object after committing triggers a fresh SELECT — and in a FastAPI endpoint the commit
    # often happens just before the response model reads those very attributes, which would
    # produce a surprising extra query or a DetachedInstanceError.
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for the lifetime of one request.

    Usage in a router::

        @router.get("/patients")
        def list_patients(db: Session = Depends(get_db)) -> ...:
            ...

    The session is always closed, and always rolled back if the request raised, so a failed
    request can never leave a partial write behind.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional session outside of a request.

    Used by the seed script, the demo reset, and the reminder job — anything that runs from the
    command line rather than from an HTTP request. Commits on success, rolls back on any
    exception::

        with session_scope() as db:
            db.add(patient)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> bool:
    """Return True if the database is reachable.

    Used by the ``/ready`` endpoint. Runs the cheapest possible statement — the point is to prove
    a connection can be established and a round trip completed, not to test anything else.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        # Any failure means "not ready" — a bad password, a refused connection, and a database
        # still starting up are all the same answer to the caller.
        return False
    else:
        return True
