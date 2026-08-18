"""``python -m app.seed`` — the command behind ``make seed``.

Also supports ``--counts-only``, which prints row counts and writes nothing. That is what
``scripts/check-seed-idempotent.sh`` compares between two runs to prove the seed does not
duplicate (SPEC §7.3).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.core.database import session_scope
from app.core.logging import configure_logging
from app.models.activity_event import ActivityEvent
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.reminder_event import ReminderEvent
from app.models.reminder_rule import ReminderRule
from app.models.user import User
from app.seed import fixtures as fx
from app.seed import seed_database

COUNTED_TABLES = {
    "organizations": Organization,
    "users": User,
    "patients": Patient,
    "reminder_rules": ReminderRule,
    "reminder_events": ReminderEvent,
    "activity_events": ActivityEvent,
}


def print_counts() -> None:
    """Print one ``table=count`` line per table, sorted.

    Deliberately plain and stable: the idempotency check compares two runs of this output with a
    string comparison, so any formatting that varied between runs would produce a false failure.
    """
    with session_scope() as session:
        for name, model in sorted(COUNTED_TABLES.items()):
            count = session.execute(select(func.count()).select_from(model)).scalar_one()
            print(f"{name}={count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed",
        description="Load the deterministic ClinicRecall demo data.",
    )
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="Print row counts and exit without writing anything.",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Skip creating the Supabase demo logins (useful with no auth service running).",
    )
    arguments = parser.parse_args(argv)

    configure_logging()

    if arguments.counts_only:
        print_counts()
        return 0

    print("Seeding ClinicRecall demo data...")
    summary = seed_database(create_auth_accounts=not arguments.no_auth)

    print("")
    print(f"  Organization      {fx.ORGANIZATION_NAME}")
    print(f"  Patients          {summary.patients}")
    print(f"  Reminder rules    {summary.reminder_rules}")
    print(f"  Reminder events   {summary.reminder_events}")
    print(f"  Activity events   {summary.activity_events}")
    print(f"  Staff accounts    {summary.users}")

    if summary.auth_skipped:
        print("")
        print("  NOTE: Supabase was not reachable, so no sign-in accounts were created.")
        print("        Patient data was seeded regardless. Run `make supabase-start`, then")
        print("        `make seed` again to enable the demo login.")
    else:
        print("")
        print("  Sign in with:")
        print(f"    {fx.DEMO_ADMIN.email}  /  {fx.DEMO_ADMIN.password}   (admin)")
        print(f"    {fx.DEMO_STAFF.email}  /  {fx.DEMO_STAFF.password}   (staff)")

    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
