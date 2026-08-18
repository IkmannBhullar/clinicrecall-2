"""Deterministic demo data (SPEC §7.3).

Run with ``make seed``. Safe to run repeatedly — see ``runner.py`` for how idempotency works.

    from app.seed import seed_database
    summary = seed_database()
"""

from app.core.database import session_scope
from app.seed.fixtures import (
    DEMO_ADMIN,
    DEMO_STAFF,
    NAMED_FIXTURES,
    ORGANIZATION_NAME,
    ORGANIZATION_SLUG,
    TOTAL_PATIENTS,
    seed_public_id,
    seed_uuid,
)
from app.seed.runner import SeedRunner, SeedSummary


def seed_database(*, create_auth_accounts: bool = True) -> SeedSummary:
    """Seed the demo organization, committing on success.

    :param create_auth_accounts: whether to create the Supabase logins. Tests turn this off, so
        the suite does not depend on the auth service being reachable.
    """
    with session_scope() as session:
        return SeedRunner(session).run(create_auth_accounts=create_auth_accounts)


__all__ = [
    "DEMO_ADMIN",
    "DEMO_STAFF",
    "NAMED_FIXTURES",
    "ORGANIZATION_NAME",
    "ORGANIZATION_SLUG",
    "TOTAL_PATIENTS",
    "SeedRunner",
    "SeedSummary",
    "seed_database",
    "seed_public_id",
    "seed_uuid",
]
