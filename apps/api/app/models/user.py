"""Staff accounts.

This table does **not** store passwords, and it never will. Authentication belongs entirely to
Supabase Auth, which owns the ``auth.users`` table in a schema Alembic is forbidden to touch
(SPEC §3.1). What lives here is the application's own view of a person: which practice they work
for, what they are called, and what they are allowed to do.

The link between the two is ``auth_user_id``. It is the hinge the whole tenancy model turns on:

    JWT.sub  →  users.auth_user_id  →  users.organization_id

That chain is resolved server-side on every request. The client never states which organization
it belongs to, so it cannot lie about it (SPEC §3.2).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.organization import Organization


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A staff member at a practice."""

    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The `sub` claim from the Supabase JWT. Unique across the installation because one Supabase
    # identity maps to exactly one application user.
    #
    # Deliberately NOT a foreign key to auth.users: Alembic must never manage or depend on
    # Supabase's schema, and a cross-schema FK would make our migrations fail on any database
    # where Supabase Auth has not been installed — including a plain test database.
    auth_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=UserRole.STAFF,
    )

    organization: Mapped[Organization] = relationship(back_populates="users")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self) -> str:
        """Two-letter initials, used wherever a name would be excessive.

        The activity feed shows initials rather than full names (SPEC §8), and
        ``activity_events.payload`` stores initials rather than names as a data-minimisation
        measure (SPEC §9).
        """
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    def __repr__(self) -> str:
        return f"<User {self.email!r} role={self.role.value}>"
