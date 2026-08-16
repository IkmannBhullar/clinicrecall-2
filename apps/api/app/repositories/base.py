"""The org-scoped repository base — the primary tenancy control.

SPEC §3.2 states the rule this file exists to enforce:

> Repository layer takes ``organization_id`` as a required first argument on every read and
> write. No repository method may be callable without it. This makes tenant leakage a type error
> rather than a review catch.

**Why the first argument, and why required.** The failure this prevents is one patient's records
appearing in another practice's account. That is the worst bug this product could have, and the
usual defence — "remember to add ``.where(organization_id == ...)``" — fails the moment somebody
writes a query in a hurry. Making the scope a required first parameter means a forgotten filter
is not a subtle data leak; it is a ``TypeError`` at the call site, raised before the process even
reaches the database.

**How it is enforced here.** Subclasses never build a ``select()`` from scratch. They start from
``self._scoped_select(organization_id)``, which already carries the filter, or from
``self._scope(statement, organization_id)`` for anything more complex. Both go through
``_organization_filter``, so there is exactly one line in the codebase that decides what "belongs
to this organization" means.

**What this is not.** Row-level security policies exist on every tenant table as well, but the
API connects with a role that bypasses them (see ``docs/SECURITY.md``). RLS is the second net,
guarding against direct database access. This layer is the primary control.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import Base


# PEP 695 type-parameter syntax (Python 3.12). `ModelT: Base` bounds the parameter to our
# declarative base, so mypy knows a model carries SQLAlchemy's machinery.
class OrganizationScopedRepository[ModelT: Base]:
    """Base class for every repository over a tenant-owned table.

    Subclasses set ``model`` and add query methods. Every such method must take
    ``organization_id`` as its first parameter and must build its statement through one of the
    scoping helpers below.
    """

    #: The SQLAlchemy model this repository reads and writes. Set by each subclass.
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

        # Fail loudly at construction rather than mysteriously at query time. A subclass that
        # forgets to set `model` would otherwise raise AttributeError somewhere deep inside a
        # statement build, with a traceback that points at the wrong place.
        if not hasattr(self, "model"):
            raise TypeError(
                f"{type(self).__name__} must set a class-level `model` attribute "
                "naming the SQLAlchemy model it operates on."
            )

    # -----------------------------------------------------------------------------------------
    # Scoping helpers — the single point where tenancy is applied
    # -----------------------------------------------------------------------------------------

    def _organization_filter(self, organization_id: uuid.UUID) -> ColumnElement[bool]:
        """Return the WHERE clause restricting results to one organization.

        Every query in the application passes through this method. If the definition of tenancy
        ever changes, this is the one line that changes with it.
        """
        # Every tenant table has this column; `getattr` is needed only because ModelT is generic.
        return getattr(self.model, "organization_id") == organization_id  # noqa: B009

    def _scoped_select(self, organization_id: uuid.UUID) -> Select[tuple[ModelT]]:
        """Start a SELECT that is already restricted to one organization.

        This is the normal entry point for a subclass::

            stmt = self._scoped_select(organization_id).where(Patient.status == status)
        """
        return select(self.model).where(self._organization_filter(organization_id))

    def _scope(self, statement: Select[Any], organization_id: uuid.UUID) -> Select[Any]:
        """Apply the organization filter to a statement built elsewhere.

        For the cases ``_scoped_select`` cannot express — aggregates, joins, custom column lists.
        """
        return statement.where(self._organization_filter(organization_id))

    # -----------------------------------------------------------------------------------------
    # Common reads
    #
    # Provided here so the routine 90% of queries cannot get tenancy wrong at all.
    # -----------------------------------------------------------------------------------------

    def get_by_id(self, organization_id: uuid.UUID, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch one row by primary key, scoped to the organization.

        Returns ``None`` — not someone else's row — when the ID belongs to another organization.
        That is the whole point: an attacker who guesses a valid UUID from another practice gets
        a 404, which is indistinguishable from the row not existing.
        """
        statement = self._scoped_select(organization_id).where(
            getattr(self.model, "id") == entity_id  # noqa: B009
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_all(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[ModelT]:
        """Fetch every row for the organization, optionally paginated."""
        statement = self._scoped_select(organization_id).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return self.session.execute(statement).scalars().all()

    def count(self, organization_id: uuid.UUID) -> int:
        """Count rows belonging to the organization."""
        statement = (
            select(func.count())
            .select_from(self.model)
            .where(self._organization_filter(organization_id))
        )
        return self.session.execute(statement).scalar_one()

    def exists(self, organization_id: uuid.UUID, entity_id: uuid.UUID) -> bool:
        """Whether a row with this ID exists *within this organization*."""
        return self.get_by_id(organization_id, entity_id) is not None

    # -----------------------------------------------------------------------------------------
    # Common writes
    # -----------------------------------------------------------------------------------------

    def add(self, organization_id: uuid.UUID, entity: ModelT) -> ModelT:
        """Stage a new row, forcing its organization to the scope it was added under.

        The assignment is not a formality. It means a caller cannot construct an object carrying
        someone else's ``organization_id`` and have it persist — whatever the object claimed, the
        scope argument wins.
        """
        setattr(entity, "organization_id", organization_id)  # noqa: B010
        self.session.add(entity)
        return entity

    def delete(self, organization_id: uuid.UUID, entity_id: uuid.UUID) -> bool:
        """Delete one row by ID, scoped to the organization.

        Returns True if a row was deleted, False if no such row exists *in this organization*.
        Fetching before deleting (rather than issuing a bulk DELETE) is what makes it impossible
        to delete another practice's data by ID.
        """
        entity = self.get_by_id(organization_id, entity_id)
        if entity is None:
            return False
        self.session.delete(entity)
        return True

    # -----------------------------------------------------------------------------------------
    # A note on what is deliberately NOT here
    #
    # There is no `flush()` passthrough. It would be convenient, but it takes no
    # `organization_id` and would therefore be the one public repository method callable without
    # a tenant scope — which breaks the invariant that
    # `test_every_public_method_takes_organization_id_first` exists to protect. Keeping the rule
    # absolute is worth more than the convenience.
    #
    # Services already hold the session (they construct the repository with it), so a service
    # that needs to flush calls `self.session.flush()` directly. That is also better layering:
    # deciding *when* a transaction is pushed to the database is a service concern, not a
    # data-access one.
    # -----------------------------------------------------------------------------------------
