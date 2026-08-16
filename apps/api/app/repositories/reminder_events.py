"""Reminder event queries.

Backs the patient timeline, the delivery-performance strip, and — via
``list_delivered_for_patient`` — the "appointments recovered" calculation on the dashboard.

The idempotency mechanism itself lives in ``ReminderService`` (phase 5), not here. This layer
provides the insert that the unique index arbitrates; deciding what to do about an
``IntegrityError`` is a domain decision, not a data-access one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select

from app.models.enums import ReminderEventStatus
from app.models.reminder_event import ReminderEvent
from app.repositories.base import OrganizationScopedRepository


class ReminderEventRepository(OrganizationScopedRepository[ReminderEvent]):
    """Reads and writes the ``reminder_events`` table."""

    model = ReminderEvent

    # -----------------------------------------------------------------------------------------
    # Per-patient reads
    # -----------------------------------------------------------------------------------------

    def list_for_patient(
        self, organization_id: uuid.UUID, patient_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[ReminderEvent]:
        """The reminder timeline shown in the patient detail drawer, newest first."""
        statement = (
            self._scoped_select(organization_id)
            .where(ReminderEvent.patient_id == patient_id)
            .order_by(ReminderEvent.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def list_delivered_for_patient(
        self, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> Sequence[ReminderEvent]:
        """Only the reminders that actually reached the patient.

        The distinction between *sent* and *delivered* is what makes the revenue figure
        defensible. SPEC §8 defines a recovered appointment as one where the patient "received ≥1
        delivered reminder" — a message the provider accepted but that then bounced does not
        count, and an office manager checking the number is right to expect that.
        """
        statement = (
            self._scoped_select(organization_id)
            .where(
                ReminderEvent.patient_id == patient_id,
                ReminderEvent.status == ReminderEventStatus.DELIVERED,
            )
            .order_by(ReminderEvent.created_at.desc())
        )
        return self.session.execute(statement).scalars().all()

    def get_last_for_patient(
        self, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> ReminderEvent | None:
        """The most recent reminder — the "Last Reminder" column on the patients list."""
        statement = (
            self._scoped_select(organization_id)
            .where(ReminderEvent.patient_id == patient_id)
            .order_by(ReminderEvent.created_at.desc())
            .limit(1)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def exists_for_rule_and_due_date(
        self,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        reminder_rule_id: uuid.UUID,
        due_date_snapshot: date,
    ) -> bool:
        """Whether this exact reminder has already been created.

        A word of warning, because this method is easy to misuse: **this is not the idempotency
        control.** SPEC §6.2 is explicit that a "have I already sent this?" query races — two job
        runs can both read False before either writes, and the patient gets two emails. The real
        guarantee is the unique index on ``(patient_id, reminder_rule_id, due_date_snapshot)``,
        which the database enforces regardless of timing.

        This method exists only for the catch-up window (SPEC §6.1), where the job needs to *ask*
        whether a missed reminder should be backfilled. A wrong answer there means a reminder is
        skipped or reconsidered, not that a duplicate is sent.
        """
        statement = (
            select(func.count())
            .select_from(ReminderEvent)
            .where(
                self._organization_filter(organization_id),
                ReminderEvent.patient_id == patient_id,
                ReminderEvent.reminder_rule_id == reminder_rule_id,
                ReminderEvent.due_date_snapshot == due_date_snapshot,
            )
        )
        return self.session.execute(statement).scalar_one() > 0

    # -----------------------------------------------------------------------------------------
    # Organization-wide reads
    # -----------------------------------------------------------------------------------------

    def list_recent(
        self, organization_id: uuid.UUID, *, limit: int = 20
    ) -> Sequence[ReminderEvent]:
        """Recent reminder activity for the dashboard feed."""
        statement = (
            self._scoped_select(organization_id)
            .order_by(ReminderEvent.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def list_failed(
        self, organization_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[ReminderEvent]:
        """Failed sends, newest first — the queue behind the failure-recovery flow (SPEC §8)."""
        statement = (
            self._scoped_select(organization_id)
            .where(ReminderEvent.status == ReminderEventStatus.FAILED)
            .order_by(ReminderEvent.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def count_by_status(self, organization_id: uuid.UUID) -> dict[ReminderEventStatus, int]:
        """Counts per delivery state — the Scheduled / Sent / Delivered / Failed strip.

        Every status is present in the result, zero-filled, so the UI never has to handle a
        missing key.
        """
        statement = (
            select(ReminderEvent.status, func.count())
            .where(self._organization_filter(organization_id))
            .group_by(ReminderEvent.status)
        )
        counts = {status: 0 for status in ReminderEventStatus}
        for status, count in self.session.execute(statement).all():
            counts[status] = count
        return counts

    def count_sent_since(self, organization_id: uuid.UUID, since: datetime) -> int:
        """How many reminders have gone out since a point in time.

        Feeds the "Reminders Sent" KPI. Counts anything that reached the provider — SENT,
        DELIVERED, or a failure that occurred after handoff — because from the practice's point
        of view the reminder was sent even if it later bounced.
        """
        statement = (
            select(func.count())
            .select_from(ReminderEvent)
            .where(
                self._organization_filter(organization_id),
                ReminderEvent.sent_at.is_not(None),
                ReminderEvent.sent_at >= since,
            )
        )
        return self.session.execute(statement).scalar_one()

    # -----------------------------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------------------------

    def create(self, organization_id: uuid.UUID, event: ReminderEvent) -> ReminderEvent:
        """Stage a new reminder event.

        Deliberately does not flush. The caller controls when the insert hits the database,
        because for rule-driven sends that moment is when the unique index either accepts the row
        or raises ``IntegrityError`` — and that is a decision point ``ReminderService`` needs to
        own explicitly.
        """
        return self.add(organization_id, event)
