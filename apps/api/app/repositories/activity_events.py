"""Activity feed queries.

A reminder about what may be written here, because it is easy to get wrong in a hurry: the
``payload`` must contain identifiers and initials only — never names, emails, or phone numbers
(SPEC §9). The ``record`` helper below is the intended way to write an event, and its docstring
repeats the rule at the point where someone would break it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from app.models.activity_event import ActivityEvent
from app.models.enums import ActivityEventType
from app.repositories.base import OrganizationScopedRepository

# Groupings behind the All / Reminders / Patients / Imports filter chips (SPEC §8).
# Defined here, next to the query that uses them, so the UI and the query cannot disagree about
# which events count as "Reminders".
FILTER_GROUPS: dict[str, tuple[ActivityEventType, ...]] = {
    "reminders": (
        ActivityEventType.REMINDER_SENT,
        ActivityEventType.REMINDER_DELIVERED,
        ActivityEventType.REMINDER_FAILED,
        ActivityEventType.REMINDERS_PAUSED,
        ActivityEventType.PATIENT_OPTED_OUT,
    ),
    "patients": (
        ActivityEventType.PATIENT_CREATED,
        ActivityEventType.PATIENT_UPDATED,
        ActivityEventType.APPOINTMENT_SCHEDULED,
        ActivityEventType.ANNUAL_VISIT_COMPLETED,
    ),
    "imports": (ActivityEventType.PATIENT_IMPORTED,),
}


class ActivityEventRepository(OrganizationScopedRepository[ActivityEvent]):
    """Reads and writes the ``activity_events`` table."""

    model = ActivityEvent

    def list_recent(
        self,
        organization_id: uuid.UUID,
        *,
        filter_group: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ActivityEvent]:
        """The activity feed, newest first, optionally narrowed to one filter group.

        An unrecognised ``filter_group`` returns everything rather than nothing. A typo in a query
        parameter should not present the user with an empty feed that looks like a data problem.
        """
        statement = self._scoped_select(organization_id)

        if filter_group and filter_group in FILTER_GROUPS:
            statement = statement.where(ActivityEvent.type.in_(FILTER_GROUPS[filter_group]))

        statement = statement.order_by(ActivityEvent.created_at.desc()).limit(limit).offset(offset)
        return self.session.execute(statement).scalars().all()

    def list_for_patient(
        self, organization_id: uuid.UUID, patient_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[ActivityEvent]:
        """One patient's history, for the detail drawer."""
        statement = (
            self._scoped_select(organization_id)
            .where(ActivityEvent.subject_patient_id == patient_id)
            .order_by(ActivityEvent.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def list_by_type(
        self, organization_id: uuid.UUID, event_type: ActivityEventType, *, limit: int = 100
    ) -> Sequence[ActivityEvent]:
        statement = (
            self._scoped_select(organization_id)
            .where(ActivityEvent.type == event_type)
            .order_by(ActivityEvent.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def record(
        self,
        organization_id: uuid.UUID,
        *,
        event_type: ActivityEventType,
        actor_user_id: uuid.UUID | None = None,
        subject_patient_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        """Write one activity event.

        **What may go in ``payload``:** identifiers, initials, enum values, counts, dates.

        **What may not:** patient or staff names, email addresses, phone numbers, or anything
        else that identifies a person (SPEC §9). This table is the one most likely to be
        exported, shipped to a log aggregator, or retained long after the record it describes was
        deleted — so it must not carry anything that would matter if it were.

        Good::

            {"patient_initials": "SJ", "rule_key": "T_MINUS_7", "channel": "EMAIL"}

        Not acceptable::

            {"patient_name": "Sarah Johnson", "email": "sarah.johnson@example.com"}

        ``actor_user_id`` of ``None`` means the system acted rather than a person — the reminder
        job, or an automatic status recompute.
        """
        event = ActivityEvent(
            type=event_type,
            actor_user_id=actor_user_id,
            subject_patient_id=subject_patient_id,
            payload=payload or {},
        )
        return self.add(organization_id, event)
