"""Row-level security — the second net.

These tests check the control described in ``docs/SECURITY.md``: RLS policies exist on every
tenant table, and they genuinely filter rows for any role that is not the table owner.

**Why this needs its own test even though RLS is not the primary control.** It is easy to write a
migration that enables RLS, see it succeed, and assume the policies work. But a policy with a
subtly wrong expression — or one attached to the wrong table — enables nothing while looking
entirely correct in ``pg_policies``. The only way to know is to connect as a role subject to the
policy and see whether rows disappear.

The mechanism used below: create a throwaway role inside the test transaction, ``SET LOCAL ROLE``
to it, and query. Because the whole test runs in a transaction that is rolled back, the role
never outlives the test.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Organization
from app.repositories import PatientRepository
from tests.conftest import make_patient

# Every table that carries tenant data. Must stay in step with TENANT_TABLES in the RLS
# migration; the first test below is what notices if it does not.
TENANT_TABLES = (
    "users",
    "patients",
    "reminder_rules",
    "reminder_events",
    "clinic_settings",
    "activity_events",
    "organizations",
)


def test_rls_is_enabled_on_every_tenant_table(db: Session) -> None:
    """No tenant table may be left without row-level security switched on."""
    rows = db.execute(
        text(
            """
            SELECT relname, relrowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relkind = 'r'
            """
        )
    ).all()
    rls_by_table = {name: enabled for name, enabled in rows}

    missing = [table for table in TENANT_TABLES if not rls_by_table.get(table)]

    assert not missing, f"Row-level security is not enabled on: {', '.join(missing)}"


def test_every_tenant_table_has_an_isolation_policy(db: Session) -> None:
    """Enabling RLS without a policy denies everything; a policy without RLS protects nothing.

    Both halves have to be present, so both are checked.
    """
    rows = db.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'")
    ).all()
    policies_by_table = {table for table, _ in rows}

    missing = [table for table in TENANT_TABLES if table not in policies_by_table]

    assert not missing, f"No RLS policy found on: {', '.join(missing)}"


def test_policies_key_on_the_organization_session_variable(db: Session) -> None:
    """The policy expression must reference the session variable, not a hardcoded value.

    A policy of ``USING (true)`` would appear in ``pg_policies`` and filter nothing at all.
    """
    rows = db.execute(
        text("SELECT tablename, qual FROM pg_policies WHERE schemaname = 'public'")
    ).all()

    for table, qual in rows:
        assert qual is not None, f"Policy on {table} has no USING expression"
        assert "app.current_organization_id" in qual, (
            f"Policy on {table} does not scope by the organization session variable. "
            f"Its expression is: {qual}"
        )


def test_policy_actually_filters_rows_for_a_non_owner_role(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """The real proof: a role subject to the policy sees only its own organization's rows.

    Everything above inspects the catalog, which tells you a policy is *installed*. This checks
    that it *works* — the only way a wrong expression gets caught.
    """
    repo = PatientRepository(db)
    repo.create(organization.id, make_patient(organization.id))
    repo.create(organization.id, make_patient(organization.id))
    repo.create(other_organization.id, make_patient(other_organization.id))
    db.flush()

    # As the table owner, RLS does not apply and all three rows are visible. This is the
    # application's normal path.
    total_as_owner = db.execute(text("SELECT count(*) FROM patients")).scalar_one()
    assert total_as_owner >= 3

    # A throwaway role with only SELECT access. `SET LOCAL` confines the change to this
    # transaction, which is rolled back when the test ends.
    db.execute(text("CREATE ROLE rls_probe NOLOGIN"))
    db.execute(text("GRANT SELECT ON patients TO rls_probe"))
    # Postgres only lets you SET ROLE to a role you are a member of, so grant membership to the
    # role we are currently connected as. Without this the SET ROLE below fails with
    # "permission denied to set role", which looks like an RLS problem but is not one.
    db.execute(text("GRANT rls_probe TO CURRENT_USER"))
    db.execute(text("SET LOCAL ROLE rls_probe"))

    try:
        # No organization declared yet: current_setting(..., true) is NULL, NULL equals nothing,
        # so the probe sees zero rows. Forgetting to set the scope denies access rather than
        # granting it — the correct direction to fail in.
        unscoped_count = db.execute(text("SELECT count(*) FROM patients")).scalar_one()
        assert unscoped_count == 0, (
            "A role with no organization set was able to read patient rows. "
            "The RLS policy is not filtering."
        )

        # Now declare an organization. Only that organization's rows become visible.
        #
        # `set_config(name, value, is_local)` rather than `SET LOCAL ...`: the SET statement is
        # parsed before parameters are bound, so `SET LOCAL x = :value` is a syntax error.
        # set_config is an ordinary function call and takes a real bind parameter — which also
        # means the value cannot be interpolated into SQL by hand.
        db.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(organization.id)},
        )
        scoped_count = db.execute(text("SELECT count(*) FROM patients")).scalar_one()
        assert scoped_count == 2

        db.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(other_organization.id)},
        )
        other_count = db.execute(text("SELECT count(*) FROM patients")).scalar_one()
        assert other_count == 1
    finally:
        # Return to the owning role so the fixture's rollback can clean up.
        db.execute(text("RESET ROLE"))
