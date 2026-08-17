"""Schemas for the reminder job and reminder events."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ReminderChannel,
    ReminderEventStatus,
    ReminderRuleKey,
    ReminderSource,
)


class JobSummaryResponse(BaseModel):
    """The structured result SPEC §6.3 requires from the reminder job.

    Every field answers a question someone asks when a run looks wrong. In particular
    ``skipped_duplicate`` is the idempotency guarantee made visible: on a second run of the same
    day it should equal what ``created`` was on the first, and ``sent`` should be zero.
    """

    evaluated: int = Field(description="Patient/rule pairs considered.")
    eligible: int = Field(description="Pairs that passed every eligibility check.")
    created: int = Field(description="reminder_events rows successfully inserted.")
    skipped_duplicate: int = Field(
        description="Insertions the unique index rejected — a reminder that already existed."
    )
    sent: int = Field(description="Messages the provider accepted.")
    failed: int = Field(description="Messages the provider rejected.")
    delivered: int = Field(description="Earlier messages confirmed delivered during this run.")
    statuses_recomputed: int = Field(
        description="Patients whose cached status was corrected before evaluation began."
    )
    errors: list[str] = Field(default_factory=list, description="Problems, safe to display.")


class ReminderRuleResponse(BaseModel):
    """One rule in the annual recall campaign."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: ReminderRuleKey
    days_relative_to_due_date: int = Field(
        description="Negative is before the due date, positive after, zero on the day."
    )
    enabled: bool


class ReminderEventResponse(BaseModel):
    """One reminder, for the patient timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ReminderEventStatus
    channel: ReminderChannel
    source: ReminderSource
    due_date_snapshot: date
    scheduled_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    failure_reason: str | None
    rendered_subject: str | None

    # The rendered bodies are deliberately absent from the list shape. They are several kilobytes
    # each, and returning them for every row of a timeline would make the response enormous for
    # content the user has not asked to see. A separate endpoint serves one message on request.


class RenderedMessageResponse(BaseModel):
    """The exact email that was sent (SPEC §6.4)."""

    model_config = ConfigDict(from_attributes=True)

    rendered_subject: str | None
    rendered_body_html: str | None
    rendered_body_text: str | None
