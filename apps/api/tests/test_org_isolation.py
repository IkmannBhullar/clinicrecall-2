"""Tenant isolation — the most important test file in the repository.

One practice seeing another practice's patients is the worst bug ClinicRecall could have. It
would be a privacy breach, it would end the sale, and — because both practices' data looks
plausible — it could go unnoticed for a long time.

Every test here follows the same shape: create records in **two** organizations, then query as
one and assert the other's records are absent. That shape matters. A missing ``WHERE`` clause
returns exactly the right answer when only one tenant exists in the database, which is why
single-tenant tests give false confidence.

The last test in this file is structural rather than behavioural: it walks the repository classes
with ``inspect`` and asserts that every public method takes ``organization_id`` as its first
parameter. That catches a leak in a method nobody has written a test for yet.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import (
    ActivityEventType,
    ClinicSettings,
    Organization,
    PatientStatus,
    ReminderRuleKey,
    UserRole,
)
from app.repositories import (
    ActivityEventRepository,
    ClinicSettingsRepository,
    OrganizationScopedRepository,
    PatientRepository,
    ReminderRuleRepository,
    UserRepository,
)
from tests.conftest import make_patient

# ---------------------------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------------------------


def test_patient_list_excludes_other_organizations(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = PatientRepository(db)

    ours = repo.create(organization.id, make_patient(organization.id, first_name="Sarah"))
    theirs = repo.create(
        other_organization.id, make_patient(other_organization.id, first_name="Intruder")
    )
    db.flush()

    results = repo.list_all(organization.id)
    result_ids = {p.id for p in results}

    assert ours.id in result_ids
    assert theirs.id not in result_ids


def test_patient_get_by_id_returns_none_across_organizations(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """A valid ID from another practice must look like a record that does not exist.

    Returning None rather than raising a distinguishable error matters: it means an attacker
    holding a real patient ID cannot tell whether the ID is invalid or simply belongs to someone
    else, so the endpoint leaks nothing even in its error behaviour.
    """
    repo = PatientRepository(db)

    theirs = repo.create(other_organization.id, make_patient(other_organization.id))
    db.flush()

    assert repo.get_by_id(organization.id, theirs.id) is None
    # Sanity check: the row genuinely exists, so we are testing scoping and not a broken insert.
    assert repo.get_by_id(other_organization.id, theirs.id) is not None


def test_patient_get_by_public_id_is_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """public_id is globally unique, which is exactly why the scope must still be applied.

    Uniqueness means an unscoped lookup would succeed — so if the ``WHERE organization_id``
    clause were ever dropped, this is the query that would quietly start serving other
    practices' patients through the URL bar.
    """
    repo = PatientRepository(db)

    theirs = repo.create(other_organization.id, make_patient(other_organization.id))
    db.flush()

    assert repo.get_by_public_id(organization.id, theirs.public_id) is None
    assert repo.get_by_public_id(other_organization.id, theirs.public_id) is not None


def test_patient_get_by_email_is_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """The same person can legitimately be a patient at two practices.

    Email is only unique *within* an organization, so this is not a hypothetical — someone who
    moves house and registers at a new clinic is the normal case.
    """
    shared_email = f"shared.person+{uuid.uuid4().hex[:8]}@example.com"

    repo = PatientRepository(db)
    ours = repo.create(
        organization.id, make_patient(organization.id, first_name="Ours", email=shared_email)
    )
    theirs = repo.create(
        other_organization.id,
        make_patient(other_organization.id, first_name="Theirs", email=shared_email),
    )
    db.flush()

    assert repo.get_by_email(organization.id, shared_email) is not None
    assert repo.get_by_email(organization.id, shared_email).id == ours.id  # type: ignore[union-attr]
    assert repo.get_by_email(other_organization.id, shared_email).id == theirs.id  # type: ignore[union-attr]


def test_patient_search_is_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = PatientRepository(db)

    repo.create(
        organization.id, make_patient(organization.id, first_name="Sarah", last_name="Johnson")
    )
    repo.create(
        other_organization.id,
        make_patient(other_organization.id, first_name="Sarah", last_name="Johnson"),
    )
    db.flush()

    results = repo.search(organization.id, query="Sarah")

    assert len(results) == 1
    assert all(p.organization_id == organization.id for p in results)


def test_patient_counts_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = PatientRepository(db)

    for _ in range(3):
        repo.create(organization.id, make_patient(organization.id))
    for _ in range(7):
        repo.create(other_organization.id, make_patient(other_organization.id))
    db.flush()

    assert repo.count(organization.id) == 3
    assert repo.count(other_organization.id) == 7


def test_patient_status_counts_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = PatientRepository(db)

    repo.create(organization.id, make_patient(organization.id, status=PatientStatus.OVERDUE))
    for _ in range(4):
        repo.create(
            other_organization.id,
            make_patient(other_organization.id, status=PatientStatus.OVERDUE),
        )
    db.flush()

    assert repo.count_by_status(organization.id)[PatientStatus.OVERDUE] == 1
    assert repo.count_by_status(other_organization.id)[PatientStatus.OVERDUE] == 4


def test_patient_delete_cannot_cross_organizations(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """Deletion is where a scoping bug is unrecoverable, so it gets its own test."""
    repo = PatientRepository(db)

    theirs = repo.create(other_organization.id, make_patient(other_organization.id))
    db.flush()

    assert repo.delete(organization.id, theirs.id) is False
    assert repo.get_by_id(other_organization.id, theirs.id) is not None


def test_add_overrides_a_forged_organization_id(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    """A patient object claiming a different organization is corrected, not honoured.

    This is the defence against a service layer that builds an entity from request data and
    forgets to strip an ``organization_id`` the client supplied. The scope argument wins.
    """
    repo = PatientRepository(db)

    # Deliberately construct the object as though it belonged to the other practice.
    forged = make_patient(other_organization.id)
    assert forged.organization_id == other_organization.id

    saved = repo.create(organization.id, forged)
    db.flush()

    assert saved.organization_id == organization.id
    assert repo.get_by_id(other_organization.id, saved.id) is None


# ---------------------------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------------------------


def test_user_lookups_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = UserRepository(db)
    email = f"admin+{uuid.uuid4().hex[:8]}@example.com"

    repo.create(
        other_organization.id,
        auth_user_id=uuid.uuid4(),
        first_name="Other",
        last_name="Admin",
        email=email,
        role=UserRole.ADMIN,
    )
    db.flush()

    assert repo.get_by_email(organization.id, email) is None
    assert repo.get_by_email(other_organization.id, email) is not None


def test_get_by_auth_user_id_is_the_documented_unscoped_exception(
    db: Session, other_organization: Organization
) -> None:
    """The one deliberately unscoped lookup — it is how the scope is discovered in the first place.

    This test exists to pin the behaviour down: it resolves a Supabase identity to a user, and
    the organization it returns is what every subsequent query in the request will be scoped by.
    """
    repo = UserRepository(db)
    auth_id = uuid.uuid4()

    created = repo.create(
        other_organization.id,
        auth_user_id=auth_id,
        first_name="Alex",
        last_name="Morgan",
        email=f"alex+{uuid.uuid4().hex[:8]}@example.com",
    )
    db.flush()

    found = repo.get_by_auth_user_id(auth_id)

    assert found is not None
    assert found.id == created.id
    # The whole point: this call is what tells us which organization to scope by.
    assert found.organization_id == other_organization.id


# ---------------------------------------------------------------------------------------------
# Settings, rules, activity
# ---------------------------------------------------------------------------------------------


def test_clinic_settings_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = ClinicSettingsRepository(db)

    repo.get_or_create(other_organization.id, clinic_name="Riverside Medical Group")
    db.flush()

    assert repo.get(organization.id) is None
    assert repo.get(other_organization.id) is not None


def test_clinic_settings_get_or_create_does_not_overwrite(
    db: Session, organization: Organization
) -> None:
    """Re-running the seed must not reset a value someone changed during a demo."""
    repo = ClinicSettingsRepository(db)

    first = repo.get_or_create(
        organization.id, clinic_name="Green Valley", annual_interval_months=18
    )
    db.flush()

    second = repo.get_or_create(
        organization.id, clinic_name="Something Else", annual_interval_months=12
    )

    assert second.organization_id == first.organization_id
    assert second.clinic_name == "Green Valley"
    assert second.annual_interval_months == 18


def test_reminder_rules_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = ReminderRuleRepository(db)

    repo.create_default_rules(organization.id)
    repo.create_default_rules(other_organization.id)
    db.flush()

    ours = repo.list_ordered(organization.id)

    assert len(ours) == 4
    assert all(rule.organization_id == organization.id for rule in ours)


def test_reminder_rules_creation_is_idempotent(db: Session, organization: Organization) -> None:
    """The seed calls this on every run (SPEC §7.3), so a second call must add nothing."""
    repo = ReminderRuleRepository(db)

    repo.create_default_rules(organization.id)
    db.flush()
    repo.create_default_rules(organization.id)
    db.flush()

    assert len(repo.list_ordered(organization.id)) == 4


def test_reminder_rules_creation_preserves_a_disabled_rule(
    db: Session, organization: Organization
) -> None:
    """If staff switched a rule off, re-seeding must not switch it back on."""
    repo = ReminderRuleRepository(db)

    repo.create_default_rules(organization.id)
    db.flush()

    rule = repo.get_by_key(organization.id, ReminderRuleKey.T_PLUS_30)
    assert rule is not None
    rule.enabled = False
    db.flush()

    repo.create_default_rules(organization.id)
    db.flush()

    refreshed = repo.get_by_key(organization.id, ReminderRuleKey.T_PLUS_30)
    assert refreshed is not None
    assert refreshed.enabled is False


def test_activity_events_are_scoped(
    db: Session, organization: Organization, other_organization: Organization
) -> None:
    repo = ActivityEventRepository(db)

    repo.record(other_organization.id, event_type=ActivityEventType.PATIENT_IMPORTED)
    db.flush()

    assert len(repo.list_recent(organization.id)) == 0
    assert len(repo.list_recent(other_organization.id)) == 1


# ---------------------------------------------------------------------------------------------
# Structural guard
# ---------------------------------------------------------------------------------------------

# The single documented exception, explained in app/repositories/users.py: this method resolves a
# Supabase identity into an organization, so it cannot take one as input.
UNSCOPED_METHOD_ALLOWLIST = {("UserRepository", "get_by_auth_user_id")}

SCOPED_REPOSITORIES: list[type[OrganizationScopedRepository]] = [  # type: ignore[type-arg]
    ActivityEventRepository,
    ClinicSettingsRepository,
    PatientRepository,
    ReminderRuleRepository,
    UserRepository,
]


@pytest.mark.parametrize("repository_class", SCOPED_REPOSITORIES, ids=lambda c: c.__name__)
def test_every_public_method_takes_organization_id_first(
    repository_class: type[OrganizationScopedRepository],  # type: ignore[type-arg]
) -> None:
    """SPEC §3.2: "No repository method may be callable without it."

    Checked structurally rather than by testing each method, so that a *new* method added months
    from now is covered the moment it is written — including one that nobody thought to test.
    """
    offenders: list[str] = []

    for name, member in inspect.getmembers(repository_class, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue  # private helpers and dunders
        if (repository_class.__name__, name) in UNSCOPED_METHOD_ALLOWLIST:
            continue

        parameters = list(inspect.signature(member).parameters)

        # parameters[0] is `self`; parameters[1] must be the tenant scope.
        if len(parameters) < 2 or parameters[1] != "organization_id":
            offenders.append(f"{repository_class.__name__}.{name}{inspect.signature(member)}")

    assert not offenders, (
        "These repository methods do not take `organization_id` as their first argument, "
        "which means they can be called without a tenant scope:\n  " + "\n  ".join(offenders)
    )


def test_settings_repository_shares_the_scoped_base(db: Session) -> None:
    """Every tenant repository must inherit the scoping machinery rather than reimplement it."""
    for repository_class in SCOPED_REPOSITORIES:
        assert issubclass(repository_class, OrganizationScopedRepository)


def test_clinic_settings_model_uses_organization_id_as_primary_key() -> None:
    """One settings row per practice, enforced by the schema rather than by convention."""
    primary_key_columns = [column.name for column in ClinicSettings.__table__.primary_key.columns]
    assert primary_key_columns == ["organization_id"]
