"""One reminder: what was sent, to whom, and what became of it.

This table is the product's memory. The patient timeline, the delivery-performance strip, the
failure-recovery flow, and the "appointments recovered" revenue calculation are all queries over
these rows.

**The unique index is the important part of this file.** SPEC §6.2 is explicit that idempotency
must not be implemented as an application-level "have I already sent this?" check, because two
job runs can both read "no" before either writes. Instead:

    UNIQUE (patient_id, reminder_rule_id, due_date_snapshot)

The job inserts the row *first*, inside the transaction, and catches ``IntegrityError`` to detect
a duplicate. The database arbitrates, so a race cannot produce two emails.

``due_date_snapshot`` is in that key for a subtle reason. Without it, a patient who completes a
visit and rolls forward to next year's due date would be blocked from ever receiving another
T_MINUS_30 reminder, because one already exists for that rule. Recording *which* due date the
reminder was computed against makes each annual cycle a distinct slot.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import (
    ReminderChannel,
    ReminderEventStatus,
    ReminderSource,
)

if TYPE_CHECKING:
    from app.models.patient import Patient
    from app.models.reminder_rule import ReminderRule


class ReminderEvent(Base, UUIDPrimaryKeyMixin):
    """A single reminder sent (or attempted) to a single patient.

    No ``TimestampMixin`` here: an event is an immutable record of something that happened, so
    ``updated_at`` would be meaningless. It has ``created_at`` and the specific timestamps that
    describe its own lifecycle.
    """

    __tablename__ = "reminder_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL for manual and test sends — see SPEC §6.2's carve-out and the note in the module
    # docstring. Postgres does not treat two NULLs as equal in a unique index, so a staff member
    # can press "Send Reminder" repeatedly without ever colliding with a rule-driven event.
    reminder_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reminder_rules.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Why this event exists. Distinguishes "manual send" from "orphaned rule" now that
    # reminder_rule_id can be NULL for two different reasons.
    source: Mapped[ReminderSource] = mapped_column(
        SAEnum(
            ReminderSource,
            name="reminder_source",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ReminderSource.RULE,
    )

    # The patient's next_annual_due_date at the moment this reminder was computed. Part of the
    # idempotency key; also what lets the timeline say "this was your 30-day reminder for the
    # visit due in March" long after the due date has rolled forward.
    due_date_snapshot: Mapped[date] = mapped_column(Date, nullable=False)

    channel: Mapped[ReminderChannel] = mapped_column(
        SAEnum(
            ReminderChannel,
            name="reminder_channel",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ReminderChannel.EMAIL,
    )

    status: Mapped[ReminderEventStatus] = mapped_column(
        SAEnum(
            ReminderEventStatus,
            name="reminder_event_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ReminderEventStatus.SCHEDULED,
    )

    # ---------------------------------------------------------------------------------------
    # Lifecycle timestamps — each is set once, when that thing actually happens, and stays NULL
    # otherwise. Reading the row tells you how far it got without having to interpret a status.
    # ---------------------------------------------------------------------------------------

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---------------------------------------------------------------------------------------
    # Provider result
    # ---------------------------------------------------------------------------------------

    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Plain-English reason a send failed, e.g. "Recipient address does not exist". Shown to staff
    # in the failure-recovery flow, so it must be readable by someone who is not an engineer.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------------------------------------------------------------------------------------
    # The rendered message
    #
    # SPEC §6.4 requires storing the rendered message so the app can display the exact email that
    # went out. That is both a strong demo beat and the honest answer to "what did you send my
    # patient?" — the alternative is re-rendering the template later and hoping nothing changed.
    # ---------------------------------------------------------------------------------------

    rendered_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rendered_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ---------------------------------------------------------------------------------------
    # Relationships
    # ---------------------------------------------------------------------------------------

    patient: Mapped[Patient] = relationship(back_populates="reminder_events")
    reminder_rule: Mapped[ReminderRule | None] = relationship(back_populates="reminder_events")

    __table_args__ = (
        # THE idempotency guarantee (SPEC §6.2). See the module docstring.
        Index(
            "uq_reminder_events_patient_rule_due_date",
            "patient_id",
            "reminder_rule_id",
            "due_date_snapshot",
            unique=True,
        ),
        # Serves the patient timeline (newest first).
        Index("ix_reminder_events_patient_id_created_at", "patient_id", "created_at"),
        # Serves the delivery-performance strip on the Reminders page.
        Index("ix_reminder_events_organization_id_status", "organization_id", "status"),
        # Serves "reminders sent this month" on the dashboard.
        Index("ix_reminder_events_organization_id_created_at", "organization_id", "created_at"),
    )

    @property
    def is_delivered(self) -> bool:
        return self.status is ReminderEventStatus.DELIVERED

    @property
    def is_failed(self) -> bool:
        return self.status is ReminderEventStatus.FAILED

    def __repr__(self) -> str:
        return f"<ReminderEvent {self.status.value} due={self.due_date_snapshot}>"
