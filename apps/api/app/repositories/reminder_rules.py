"""Reminder rule queries.

Small by design: there are exactly four rules per organization and the only thing staff can do
with them is toggle each on or off (SPEC §8).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.enums import ReminderRuleKey
from app.models.reminder_rule import DEFAULT_RULE_OFFSETS, ReminderRule
from app.repositories.base import OrganizationScopedRepository


class ReminderRuleRepository(OrganizationScopedRepository[ReminderRule]):
    """Reads and writes the ``reminder_rules`` table."""

    model = ReminderRule

    def list_ordered(self, organization_id: uuid.UUID) -> Sequence[ReminderRule]:
        """All four rules in campaign order: 30 days before, 7 before, on the day, 30 after.

        Ordered by the offset rather than by name or creation time, so the Reminders page always
        reads as a timeline regardless of what order the rows were written in.
        """
        statement = self._scoped_select(organization_id).order_by(
            ReminderRule.days_relative_to_due_date.asc()
        )
        return self.session.execute(statement).scalars().all()

    def list_enabled(self, organization_id: uuid.UUID) -> Sequence[ReminderRule]:
        """Only the rules currently switched on — what the reminder job evaluates."""
        statement = (
            self._scoped_select(organization_id)
            .where(ReminderRule.enabled.is_(True))
            .order_by(ReminderRule.days_relative_to_due_date.asc())
        )
        return self.session.execute(statement).scalars().all()

    def get_by_key(self, organization_id: uuid.UUID, key: ReminderRuleKey) -> ReminderRule | None:
        statement = self._scoped_select(organization_id).where(ReminderRule.key == key)
        return self.session.execute(statement).scalar_one_or_none()

    def create_default_rules(self, organization_id: uuid.UUID) -> list[ReminderRule]:
        """Create the four standard rules for a new practice.

        Idempotent: a rule that already exists is left exactly as it is, including its enabled
        state. That matters because the seed calls this on every run (SPEC §7.3), and a demo
        where someone had switched T_PLUS_30 off should not have it silently switched back on.
        """
        rules: list[ReminderRule] = []

        for key, offset in DEFAULT_RULE_OFFSETS.items():
            existing = self.get_by_key(organization_id, key)
            if existing is not None:
                rules.append(existing)
                continue

            rule = ReminderRule(
                key=key,
                days_relative_to_due_date=offset,
                enabled=True,
                template_id="annual_recall",
            )
            rules.append(self.add(organization_id, rule))

        return rules
