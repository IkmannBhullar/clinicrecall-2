"""Organization lookups.

The only repository in this package that is *not* org-scoped, because an organization is the
scope. There is nothing above it to be scoped by.

For that reason its methods are deliberately few. Anything that reads tenant-owned data belongs
in one of the scoped repositories, where the tenancy rule applies.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Organization


class OrganizationRepository:
    """Reads and writes the ``organizations`` table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, organization_id: uuid.UUID) -> Organization | None:
        return self.session.get(Organization, organization_id)

    def get_by_slug(self, slug: str) -> Organization | None:
        """Look up a practice by its URL-safe identifier, e.g. "green-valley-family-clinic".

        Used by the seed script to decide whether the demo organization already exists, which is
        what makes re-running the seed idempotent (SPEC §7.3).
        """
        statement = select(Organization).where(Organization.slug == slug)
        return self.session.execute(statement).scalar_one_or_none()

    def create(self, *, name: str, slug: str) -> Organization:
        organization = Organization(name=name, slug=slug)
        self.session.add(organization)
        return organization
