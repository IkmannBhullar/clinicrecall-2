"""The tenant.

An organization is one medical practice. It is the root of every ownership chain in the database:
patients, users, settings, reminders, and activity all hang off exactly one organization, and no
query in the application is allowed to run without naming which one (SPEC §3.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    # Imported only for type checking to avoid a circular import at runtime: these modules import
    # Organization back. The string form in Mapped["..."] is resolved lazily by SQLAlchemy.
    from app.models.clinic_settings import ClinicSettings
    from app.models.patient import Patient
    from app.models.user import User


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A medical practice using ClinicRecall."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # A short URL-safe identifier, e.g. "green-valley-family-clinic". Unique across the whole
    # installation, which is why it has no organization_id scope — it *is* the scope.
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    # ---------------------------------------------------------------------------------------
    # Relationships
    # ---------------------------------------------------------------------------------------
    # Every child is cascade-deleted. Removing a practice must not leave orphaned patient rows
    # behind, and `make demo-reset` relies on being able to clear a tenant cleanly.

    users: Mapped[list[User]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    patients: Mapped[list[Patient]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    # One row per organization, so this is a scalar rather than a list.
    settings: Mapped[ClinicSettings | None] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Organization {self.slug!r}>"
