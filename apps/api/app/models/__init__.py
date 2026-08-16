"""SQLAlchemy table definitions.

Every model is re-exported here for one practical reason beyond convenience: Alembic's
autogenerate compares the live database against ``Base.metadata``, and a model class that has
never been imported is not registered on that metadata. It would then be silently absent from
every migration — and the mismatch would only surface later, as a missing-table error.

Importing them all here, and importing this package from ``migrations/env.py``, makes that
failure impossible.
"""

from app.models.activity_event import ActivityEvent
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.clinic_settings import ClinicSettings
from app.models.enums import (
    ActivityEventType,
    PatientStatus,
    PreferredContactMethod,
    ReminderChannel,
    ReminderEventStatus,
    ReminderRuleKey,
    ReminderSource,
    UserRole,
)
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.reminder_event import ReminderEvent
from app.models.reminder_rule import DEFAULT_RULE_OFFSETS, ReminderRule
from app.models.user import User

__all__ = [
    "DEFAULT_RULE_OFFSETS",
    "ActivityEvent",
    "ActivityEventType",
    "Base",
    "ClinicSettings",
    "Organization",
    "Patient",
    "PatientStatus",
    "PreferredContactMethod",
    "ReminderChannel",
    "ReminderEvent",
    "ReminderEventStatus",
    "ReminderRule",
    "ReminderRuleKey",
    "ReminderSource",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserRole",
]
