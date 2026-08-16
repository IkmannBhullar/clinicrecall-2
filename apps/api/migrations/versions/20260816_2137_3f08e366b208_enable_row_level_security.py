"""enable row level security

Revision ID: 3f08e366b208
Revises: 46d923e0d3c8
Created: 2026-08-16 21:37

Row-level security as defence in depth (SPEC §3.2).

READ THIS BEFORE RELYING ON IT
------------------------------
These policies are **not** the application's primary tenancy control, and it would be a serious
mistake to treat them as such. The API connects to Postgres as the ``postgres`` role, which owns
these tables and therefore bypasses RLS entirely. Nothing below affects a normal API request.

The primary control is the repository layer, where ``organization_id`` is a required first
argument on every method — see ``app/repositories/base.py``.

Concretely: RLS is enabled but **not** FORCEd. In Postgres, a table's owner is exempt from its
policies unless FORCE is set, so the application — which connects as the owning role — proceeds
exactly as before. Any other role is subject to them. That is precisely the arrangement SPEC §3.2
describes, and it is achieved without needing a superuser to grant BYPASSRLS (the ``postgres``
role in a Supabase stack is privileged but not a true superuser, so ``ALTER ROLE ... BYPASSRLS``
is not available).

So what are these policies for? They guard the paths that do not go through our code:

* A Supabase Studio session, or anything else connecting with the ``anon`` or ``authenticated``
  roles, cannot read patient data at all.
* A future feature that queries Postgres directly with a non-owner role inherits the correct
  scoping automatically rather than having to remember it.
* If the application is ever run with a non-superuser role — which is what a real production
  deployment should do — these policies become live, and the tenancy rule is enforced by the
  database as well as by the code.

HOW THE POLICIES DECIDE
-----------------------
Each policy compares the row's ``organization_id`` against a session variable:

    current_setting('app.current_organization_id', true)::uuid

The ``true`` argument means "return NULL if the setting is missing" rather than raising. A NULL
never equals anything, so a connection that has not declared an organization sees zero rows —
which is the correct failure mode. Forgetting to set the scope denies access; it does not grant
it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic to order migrations.
revision: str = "3f08e366b208"
down_revision: str | None = "46d923e0d3c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that carry an `organization_id` column and are therefore scoped by it directly.
TENANT_TABLES: tuple[str, ...] = (
    "users",
    "patients",
    "reminder_rules",
    "reminder_events",
    "clinic_settings",
    "activity_events",
)

# The organizations table is scoped by its own primary key rather than by an organization_id
# column, so it needs a policy of a slightly different shape.
ORGANIZATION_TABLE = "organizations"

# The SQL expression yielding the current connection's organization, or NULL when unset.
CURRENT_ORG = "current_setting('app.current_organization_id', true)::uuid"


def upgrade() -> None:
    """Enable RLS and install one org-scoping policy per table."""
    for table in TENANT_TABLES:
        # ENABLE turns policies on for every role except the table's owner.
        #
        # FORCE (which would also subject the owner) is deliberately NOT set — see the module
        # docstring. The application connects as the owner and does its scoping in Python; making
        # it subject to these policies would break every query it makes.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")

        # A single FOR ALL policy covering SELECT, INSERT, UPDATE and DELETE.
        #
        #   USING       — which existing rows this connection may see or modify.
        #   WITH CHECK  — which rows it may write. Without this half, a connection scoped to one
        #                 organization could INSERT a row belonging to another.
        op.execute(
            f"""
            CREATE POLICY {table}_organization_isolation ON {table}
                FOR ALL
                USING (organization_id = {CURRENT_ORG})
                WITH CHECK (organization_id = {CURRENT_ORG});
            """
        )

    # Organizations: the row's own id is the scope.
    op.execute(f"ALTER TABLE {ORGANIZATION_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {ORGANIZATION_TABLE}_self_isolation ON {ORGANIZATION_TABLE}
            FOR ALL
            USING (id = {CURRENT_ORG})
            WITH CHECK (id = {CURRENT_ORG});
        """
    )

    # ------------------------------------------------------------------------------------------
    # Note on table privileges.
    #
    # No GRANT is issued to Supabase's `anon` or `authenticated` roles, so those roles cannot
    # reach these tables at all — they are stopped by table privileges before RLS is even
    # consulted. That is intentional and stronger than relying on a policy: patient data is
    # served exclusively by the FastAPI backend, never by PostgREST.
    #
    # The policies therefore matter for any *future* role that is granted table access, which is
    # exactly the "second net" role SPEC §3.2 assigns them.
    # ------------------------------------------------------------------------------------------


def downgrade() -> None:
    """Remove the policies and disable RLS."""
    op.execute(f"DROP POLICY IF EXISTS {ORGANIZATION_TABLE}_self_isolation ON {ORGANIZATION_TABLE};")
    op.execute(f"ALTER TABLE {ORGANIZATION_TABLE} DISABLE ROW LEVEL SECURITY;")

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_organization_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
