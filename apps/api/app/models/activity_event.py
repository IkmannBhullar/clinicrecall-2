"""The activity feed and audit trail.

Every meaningful action writes one row here: an import ran, a reminder was delivered, an
appointment was marked scheduled, a patient opted out.

**The payload is deliberately impoverished.** SPEC §9 requires that ``payload`` hold identifiers
and initials — never names, email addresses, or phone numbers. The reasoning is data
minimisation: an audit log is the table most likely to be exported, shipped to a log aggregator,
or kept long after the record it describes was deleted. Anything stored here should be assumed to
outlive its usefulness.

So a payload looks like this:

    {"patient_initials": "SJ", "rule_key": "T_MINUS_7", "channel": "EMAIL"}

and never like this:

    {"patient_name": "Sarah Johnson", "email": "sarah.johnson@example.com"}

The feed renders a readable sentence by joining ``subject_patient_id`` to the live patient row at
display time, which means the log itself carries nothing sensitive.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ActivityEventType

if TYPE_CHECKING:
    pass


class ActivityEvent(Base, UUIDPrimaryKeyMixin):
    """One recorded action.

    Append-only: there is no ``updated_at`` because an audit record that can be edited is not an
    audit record.
    """

    __tablename__ = "activity_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL means the system did it rather than a person — the nightly reminder job, or an
    # automatic status recompute. The feed renders these as "ClinicRecall" rather than inventing
    # a fake actor.
    #
    # ON DELETE SET NULL rather than CASCADE: if a staff member leaves and their user row is
    # removed, the history of what happened must survive. Losing the audit trail because someone
    # changed jobs would be a bug.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    type: Mapped[ActivityEventType] = mapped_column(
        SAEnum(
            ActivityEventType,
            name="activity_event_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # Which patient this concerns, if any. NULL for organization-wide events such as a settings
    # change or an import summary.
    subject_patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Minimised structured detail — see the module docstring for what may and may not go in here.
    # JSONB rather than JSON so it can be queried and indexed if a future feature needs to.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The activity feed is "this organization's events, newest first" — this index is that
        # query. It is the only read pattern this table has.
        Index("ix_activity_events_organization_id_created_at", "organization_id", "created_at"),
        # Supports the per-patient history shown in the detail drawer.
        Index("ix_activity_events_subject_patient_id", "subject_patient_id"),
        # Supports the All / Reminders / Patients / Imports filter chips.
        Index("ix_activity_events_organization_id_type", "organization_id", "type"),
    )

    def __repr__(self) -> str:
        return f"<ActivityEvent {self.type.value} at {self.created_at}>"
