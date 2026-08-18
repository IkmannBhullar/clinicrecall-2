"""``make demo-reset`` — restore pristine demo state (SPEC constraint D3).

    "make demo-reset restores pristine seed state in under 30 seconds, and an admin-only
     'Reset demo data' control exists in Settings."

Every demo mutates the data: reminders get sent, appointments get marked scheduled, a messy CSV
gets imported. Before the next clinic sees the product, all of that has to go and the six named
fixtures have to be back in exactly the states the talk track claims.

**Why TRUNCATE rather than dropping the database.** Tearing down and rebuilding the Supabase
containers takes minutes and would blow the 30-second budget many times over. Truncating the
application tables and re-seeding takes a second or two.

**What is deliberately left alone:**

* ``auth.*`` — Supabase owns it. The demo login must keep working, and recreating accounts is both
  slow and pointless.
* ``alembic_version`` — truncating it would make the database look unmigrated, and the next
  ``alembic upgrade head`` would try to create tables that already exist.
* The containers themselves.
"""

from __future__ import annotations

import logging
import sys
import time

from sqlalchemy import text

from app.core.database import session_scope
from app.core.logging import configure_logging
from app.seed import seed_database

logger = logging.getLogger(__name__)

#: Application tables, in no particular order — CASCADE handles the foreign keys.
TABLES_TO_CLEAR = (
    "activity_events",
    "reminder_events",
    "reminder_rules",
    "patients",
    "clinic_settings",
    "users",
    "organizations",
)


def clear_application_data() -> None:
    """Empty every application table.

    One statement rather than seven. A single TRUNCATE takes one lock and one pass; seven
    separate ones can deadlock against each other's foreign keys, and would be slower besides.
    """
    with session_scope() as session:
        session.execute(text(f"TRUNCATE TABLE {', '.join(TABLES_TO_CLEAR)} CASCADE"))

    logger.info("Cleared %d application tables", len(TABLES_TO_CLEAR))


def reset_demo_data(*, create_auth_accounts: bool = True) -> float:
    """Wipe and re-seed. Returns how long it took, in seconds."""
    started = time.perf_counter()

    clear_application_data()
    seed_database(create_auth_accounts=create_auth_accounts)

    return time.perf_counter() - started


def main() -> int:
    configure_logging()

    print("Clearing application data...")
    elapsed = reset_demo_data()

    print(f"Demo data reset in {elapsed:.1f}s.")

    # SPEC D3 names 30 seconds. A warning rather than a failure: a slow reset on a cold machine
    # is still a working reset, but you want to know before you are standing in a clinic.
    if elapsed > 30:
        print("")
        print("WARNING: that exceeded the 30 second budget in SPEC D3.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
