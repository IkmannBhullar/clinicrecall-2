"""Migration hygiene.

These are static checks over the migration files rather than tests that run them. Running the
full up/down cycle in pytest would be slow and would fight the transaction-per-test fixture; but
the specific mistakes that bite are easy to detect by reading the files.

The enum check exists because of a bug found while building this phase: Alembic's autogenerate
creates native enum types on the way up but does **not** drop them on the way down. It emits
``DROP TABLE`` and stops. The result is that ``alembic downgrade base`` succeeds, leaves eight
orphaned types behind, and the next ``alembic upgrade head`` fails with::

    type "preferred_contact_method" already exists

So the downgrade path silently becomes a one-way door — which you discover at the worst possible
moment, while trying to rebuild a database in a hurry. The initial migration now drops the types
explicitly, and this test makes sure a *newly added* enum cannot reintroduce the problem.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import Enum as SAEnum

from app.models import Base

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _all_migration_source() -> str:
    """Every migration file concatenated, so a check can look across the whole history."""
    return "\n".join(path.read_text() for path in MIGRATIONS_DIR.glob("*.py"))


def _enum_type_names() -> set[str]:
    """Every named native enum type the models define.

    Read from SQLAlchemy's metadata rather than from a hand-maintained list, so a new enum is
    picked up the moment a model uses it — which is the whole point.
    """
    names: set[str] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, SAEnum) and column.type.name:
                names.add(column.type.name)
    return names


def test_at_least_one_migration_exists() -> None:
    assert list(MIGRATIONS_DIR.glob("*.py")), "No migrations found — the schema is unmanaged."


def test_every_enum_type_is_dropped_on_downgrade() -> None:
    """A native enum created by a migration must also be dropped by one.

    Otherwise ``downgrade base`` followed by ``upgrade head`` fails on the leftover type.
    """
    source = _all_migration_source()

    missing = [
        name
        for name in _enum_type_names()
        if not re.search(rf"DROP TYPE IF EXISTS\b.*\b{re.escape(name)}\b", source)
        and f'"{name}"' not in _downgrade_enum_list(source)
    ]

    assert not missing, (
        "These enum types are created by a migration but never dropped:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nAdd them to the DROP TYPE loop in the migration's downgrade(), or "
        "`alembic downgrade base && alembic upgrade head` will fail on the leftover type."
    )


def _downgrade_enum_list(source: str) -> str:
    """Extract the body of the ``for enum_name in (...)`` loop, if the migration uses one.

    The initial migration drops its types with a loop rather than eight separate statements, so a
    plain regex for ``DROP TYPE IF EXISTS <name>`` would not find them. This pulls out the tuple
    so the names inside it can be matched.
    """
    match = re.search(r"for enum_name in \((.*?)\)", source, re.DOTALL)
    return match.group(1) if match else ""


def test_no_migration_touches_the_supabase_auth_schema() -> None:
    """SPEC §3.1: "Alembic must never migrate auth.*".

    Supabase's GoTrue owns those tables. A migration that altered them would break authentication
    in a way that is hard to diagnose and impossible to undo cleanly.
    """
    source = _all_migration_source()

    forbidden = [
        pattern
        for pattern in ("auth.users", "auth.identities", "auth.sessions", 'schema="auth"')
        if pattern in source
    ]

    assert not forbidden, (
        f"A migration references Supabase's auth schema: {forbidden}. Alembic owns `public` only."
    )


def test_migrations_have_a_single_linear_history() -> None:
    """No branches. A branched history needs a merge revision and confuses `upgrade head`."""
    down_revisions: list[str | None] = []

    for path in MIGRATIONS_DIR.glob("*.py"):
        match = re.search(r"^down_revision: str \| None = (.+)$", path.read_text(), re.MULTILINE)
        assert match, f"{path.name} has no down_revision"
        value = match.group(1).strip()
        down_revisions.append(None if value == "None" else value)

    # Exactly one migration may be the root (down_revision = None).
    roots = [revision for revision in down_revisions if revision is None]
    assert len(roots) == 1, f"Expected exactly one root migration, found {len(roots)}"

    # No two migrations may share a parent, which is what a branch looks like.
    parents = [revision for revision in down_revisions if revision is not None]
    assert len(parents) == len(set(parents)), (
        "Two migrations share a parent — the history branches."
    )
