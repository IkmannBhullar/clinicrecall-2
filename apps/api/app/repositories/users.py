"""Staff account lookups.

``get_by_auth_user_id`` is the most security-sensitive method in this package. It is the step
that turns a verified Supabase token into an organization:

    JWT.sub  →  users.auth_user_id  →  users.organization_id

Everything downstream trusts the organization this returns, so this lookup — and never a value
from the request body, a query parameter, or a header — is where tenancy originates (SPEC §3.2).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import OrganizationScopedRepository


class UserRepository(OrganizationScopedRepository[User]):
    """Reads and writes the ``users`` table."""

    model = User

    # -----------------------------------------------------------------------------------------
    # Unscoped lookup — the single legitimate exception in this package
    # -----------------------------------------------------------------------------------------

    def get_by_auth_user_id(self, auth_user_id: uuid.UUID) -> User | None:
        """Resolve a Supabase identity to an application user.

        This method takes no ``organization_id``, and it is the only one in the package that
        does not. That is not an oversight — it is the bootstrap step. At the moment it runs, the
        organization is precisely what we do not yet know; this lookup is how we find it out.

        The value it returns is what every other repository call in the request will be scoped
        by, which is why ``auth_user_id`` carries a unique constraint: one Supabase identity maps
        to exactly one application user, so the answer can never be ambiguous.
        """
        statement = select(User).where(User.auth_user_id == auth_user_id)
        return self.session.execute(statement).scalar_one_or_none()

    # -----------------------------------------------------------------------------------------
    # Scoped queries
    # -----------------------------------------------------------------------------------------

    def get_by_email(self, organization_id: uuid.UUID, email: str) -> User | None:
        """Find a staff member by email within one practice.

        Compared case-insensitively: nobody thinks of ``Alex@clinic.com`` and
        ``alex@clinic.com`` as two different colleagues.
        """
        statement = self._scoped_select(organization_id).where(
            func.lower(User.email) == email.lower()
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_by_role(self, organization_id: uuid.UUID, role: UserRole) -> Sequence[User]:
        statement = self._scoped_select(organization_id).where(User.role == role)
        return self.session.execute(statement).scalars().all()

    def create(
        self,
        organization_id: uuid.UUID,
        *,
        auth_user_id: uuid.UUID,
        first_name: str,
        last_name: str,
        email: str,
        role: UserRole = UserRole.STAFF,
    ) -> User:
        user = User(
            auth_user_id=auth_user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role,
        )
        # Routed through the base class so organization_id is stamped by the scope argument
        # rather than by whatever the caller happened to pass in.
        return self.add(organization_id, user)
