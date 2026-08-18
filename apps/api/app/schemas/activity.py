"""Activity feed shapes (SPEC §8)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ActivityEventType


class ActivityEntry(BaseModel):
    """One line of the feed.

    Carries **initials, never names** (SPEC §8: "initials rather than full names in the
    high-level list"). The stored payload holds no names either (SPEC §9) — the initials here are
    derived at render time by joining to the live patient row, so the audit log itself stays free
    of anything identifying.
    """

    id: str
    type: ActivityEventType
    created_at: datetime

    actor_initials: str | None = Field(
        default=None,
        description="Who did it. Null means the system acted — the reminder job, or a recompute.",
    )
    patient_initials: str | None = None
    patient_public_id: str | None = Field(
        default=None, description="So a feed line can open the patient it concerns."
    )

    summary: str = Field(description="A readable sentence, already assembled for display.")
    payload: dict[str, Any] = Field(default_factory=dict)


class ActivityResponse(BaseModel):
    entries: list[ActivityEntry]
    has_more: bool = Field(description="Whether another page exists, for the 'Load more' control.")
