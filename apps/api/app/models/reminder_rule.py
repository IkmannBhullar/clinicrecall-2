"""The four reminder rules that make up the annual recall campaign.

Each organization gets exactly four rows, created when the practice is set up: 30 days before the
due date, 7 days before, on the day, and 30 days after. Staff can turn each one on or off. That
is the entire configuration surface.

This is deliberately not a rules engine. SPEC §1 rules out workflow and automation builders, and
SPEC §8 specifies "four rules with enable/disable toggles only". The offset is a column rather
than a constant purely so the seed and the tests can express "30 days before" once.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReminderRuleKey

if TYPE_CHECKING:
    from app.models.reminder_event import ReminderEvent

# The campaign, as shipped. The seed creates exactly these four rows for every organization.
# Keeping the mapping here rather than in the seed means the tests, the seed, and any future
# organization-creation path all agree by construction.
DEFAULT_RULE_OFFSETS: dict[ReminderRuleKey, int] = {
    ReminderRuleKey.T_MINUS_30: -30,
    ReminderRuleKey.T_MINUS_7: -7,
    ReminderRuleKey.T_ZERO: 0,
    ReminderRuleKey.T_PLUS_30: 30,
}


class ReminderRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One scheduled point in the annual recall campaign."""

    __tablename__ = "reminder_rules"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    key: Mapped[ReminderRuleKey] = mapped_column(
        SAEnum(
            ReminderRuleKey,
            name="reminder_rule_key",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # Days relative to the patient's next_annual_due_date. Negative is before, positive is after,
    # zero is on the day. Signed rather than a separate before/after flag because the arithmetic
    # then reads exactly as the rule does: fire when today == due_date + offset.
    days_relative_to_due_date: Mapped[int] = mapped_column(Integer, nullable=False)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Which email template to render. There is one template today ("annual_recall"); the column
    # exists because SPEC §4.1 lists it and because a second template is the one plausible
    # extension that would not be scope creep.
    template_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="annual_recall", server_default="annual_recall"
    )

    reminder_events: Mapped[list[ReminderEvent]] = relationship(back_populates="reminder_rule")

    __table_args__ = (
        # One rule per key per organization. Without this a bug in the seed or in a future
        # organization-creation path could produce two T_MINUS_30 rules, and every eligible
        # patient would receive the same reminder twice.
        UniqueConstraint("organization_id", "key", name="uq_reminder_rules_organization_id_key"),
    )

    def __repr__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"<ReminderRule {self.key.value} {self.days_relative_to_due_date:+d}d {state}>"
