"""Per-practice configuration.

One row per organization — the primary key *is* the foreign key, which is how the schema enforces
that a practice cannot have two conflicting sets of settings.

Two of these columns are load-bearing rather than cosmetic:

* ``timezone``               — decides what "today" means for this practice (SPEC §5.3).
* ``annual_interval_months`` — decides when every patient is next due (SPEC §5.3).

Both are read by ``RecallService``. Neither is a constant anywhere in the codebase, because a
practice that runs an 18-month recall cycle is a legitimate customer and a hardcoded 12 would
silently give them wrong dates for every patient.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class ClinicSettings(Base, TimestampMixin):
    """Configuration for one practice."""

    __tablename__ = "clinic_settings"

    # Primary key and foreign key in one column. There is exactly one settings row per
    # organization, and the schema makes a second one impossible rather than merely unlikely.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # ---------------------------------------------------------------------------------------
    # Clinic profile — these values appear in the reminder email a patient receives, so they are
    # the practice's public face rather than internal settings.
    # ---------------------------------------------------------------------------------------

    clinic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Where the "Schedule Appointment" button in the reminder email points. ClinicRecall does not
    # do scheduling (SPEC §1) — it hands the patient off to whatever the practice already uses.
    scheduling_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---------------------------------------------------------------------------------------
    # Recall configuration
    # ---------------------------------------------------------------------------------------

    # IANA timezone name, e.g. "America/Los_Angeles". Not an offset: offsets change twice a year
    # and a stored "-08:00" is wrong for half of it.
    #
    # This is what stops a demo at 9pm Pacific from showing tomorrow's statuses (SPEC §5.3).
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="America/Los_Angeles",
        server_default="America/Los_Angeles",
    )

    annual_interval_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default=text("12")
    )

    # What one recovered annual visit is worth to the practice. Feeds the "Estimated Revenue
    # Recovered" figure on the dashboard.
    #
    # Numeric, never a float: 0.1 + 0.2 is not 0.3 in binary floating point, and an office
    # manager checking the arithmetic on a currency figure will notice.
    estimated_annual_visit_value: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("250.00"), server_default=text("250.00")
    )

    # Sign-off appended to every reminder, e.g. "Warm regards,\nThe Green Valley team".
    reminder_signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="settings")

    __table_args__ = (
        # A zero or negative interval would make every patient permanently overdue; a 120-month
        # interval is almost certainly a typo. Enforced in the database because RecallService
        # divides the world by this number and there is no sensible behaviour for a bad value.
        CheckConstraint(
            "annual_interval_months > 0 AND annual_interval_months <= 60",
            name="annual_interval_months_range",
        ),
        CheckConstraint(
            "estimated_annual_visit_value >= 0",
            name="estimated_annual_visit_value_non_negative",
        ),
    )

    def __repr__(self) -> str:
        return f"<ClinicSettings {self.clinic_name!r} tz={self.timezone}>"
