"""Enumerations shared by the models, services, and schemas.

Every enum here inherits from ``str`` as well as ``Enum``. That means ``PatientStatus.OVERDUE``
compares equal to ``"OVERDUE"``, serialises to JSON as a plain string, and reads legibly in a
database row — while still being a real type that mypy can check and that cannot hold a typo.

The stored value always equals the member name. Keeping those identical means a value read
straight out of Postgres is the same token used in Python and in the API response, so there is
nothing to translate and nothing to get out of step.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    """What a signed-in staff member is allowed to do.

    Only two levels, deliberately. SPEC §1 rules out multi-step approval flows and anything that
    smells like a permissions builder; a clinic has staff and it has an office manager.
    """

    ADMIN = "ADMIN"
    STAFF = "STAFF"


class PatientStatus(str, enum.Enum):
    """Where a patient sits in the annual-recall cycle.

    This is a *derived* value (SPEC §5.1): it is computed by ``RecallService.compute_status`` and
    cached on the patient row. Nothing else in the codebase may write it. The bands themselves,
    and the order they are checked in, are defined in SPEC §5.2 and implemented in phase 3.
    """

    ACTIVE = "ACTIVE"
    """Not due for a while — more than 30 days out."""

    DUE_SOON = "DUE_SOON"
    """Due within the next 30 days."""

    DUE = "DUE"
    """Due now, or up to 7 days past due."""

    OVERDUE = "OVERDUE"
    """More than 7 days past due."""

    SCHEDULED = "SCHEDULED"
    """Has an upcoming appointment booked."""

    COMPLETED = "COMPLETED"
    """Seen within the last 30 days. Decays back to ACTIVE automatically."""

    INACTIVE = "INACTIVE"
    """Reminders paused, or the patient has opted out."""


class PreferredContactMethod(str, enum.Enum):
    """How a patient prefers to be reached.

    ``SMS`` exists so the column can hold an imported preference without losing information, but
    SMS delivery is explicitly out of scope (SPEC §1 and §6.4). A patient whose preference is SMS
    is still reminded by email; the preference is recorded, not acted on.
    """

    EMAIL = "EMAIL"
    SMS = "SMS"
    PHONE = "PHONE"


class ReminderRuleKey(str, enum.Enum):
    """The four fixed points in the recall campaign.

    A stable enum rather than free text, because these keys are referenced by the seed, the demo
    script, and the idempotency index. There is no rule builder and there will not be one — the
    campaign is four toggles (SPEC §8, Reminders).
    """

    T_MINUS_30 = "T_MINUS_30"
    """30 days before the annual visit is due."""

    T_MINUS_7 = "T_MINUS_7"
    """7 days before."""

    T_ZERO = "T_ZERO"
    """On the due date."""

    T_PLUS_30 = "T_PLUS_30"
    """30 days after, for patients who have not responded."""


class ReminderChannel(str, enum.Enum):
    """How a reminder was delivered.

    ``SMS`` is present but unimplemented, exactly as SPEC §6.4 instructs. Defining it now means
    the column and the API contract do not have to change if it is ever built; it does not mean
    anything sends one.
    """

    EMAIL = "EMAIL"
    SMS = "SMS"


class ReminderSource(str, enum.Enum):
    """What caused a reminder to be created.

    Not in the original spec's column list — added to resolve SPEC §6.2's manual-send carve-out.
    A rule-driven reminder is deduplicated by the unique index on
    ``(patient_id, reminder_rule_id, due_date_snapshot)``. A manual send carries a NULL rule ID,
    which Postgres treats as never conflicting, so pressing "Send Reminder" during a demo can
    never raise a duplicate error on stage. This column records *why* the rule ID is null, so
    "manual send" and "data problem" are distinguishable.
    """

    RULE = "RULE"
    """Created by the scheduled reminder job, from one of the four campaign rules."""

    MANUAL = "MANUAL"
    """A staff member pressed "Send Reminder" on the patient detail view."""

    TEST = "TEST"
    """A "Send Test Reminder" from the Reminders settings page."""


class ReminderEventStatus(str, enum.Enum):
    """Delivery state of a single reminder.

    The happy path is SCHEDULED → SENDING → SENT → DELIVERED. Every state is recorded rather than
    overwritten so the patient timeline can show what actually happened, including failures —
    which is the point of the failure-recovery flow in SPEC §8.
    """

    SCHEDULED = "SCHEDULED"
    """Row created, not yet handed to the provider."""

    SENDING = "SENDING"
    """Handed to the provider, awaiting a result."""

    SENT = "SENT"
    """The provider accepted it."""

    DELIVERED = "DELIVERED"
    """The provider confirmed it reached the mailbox."""

    FAILED = "FAILED"
    """Rejected or bounced. ``failure_reason`` explains why."""

    CANCELLED = "CANCELLED"
    """Superseded before sending — for example, the patient booked an appointment."""


class ActivityEventType(str, enum.Enum):
    """What happened, for the activity feed and the audit trail.

    ``PATIENT_OPTED_OUT`` is not in SPEC §4.1's list. It is added because SPEC §6.5 requires the
    unsubscribe endpoint to write an activity event, and none of the listed types describes that.
    """

    PATIENT_IMPORTED = "PATIENT_IMPORTED"
    PATIENT_CREATED = "PATIENT_CREATED"
    PATIENT_UPDATED = "PATIENT_UPDATED"
    REMINDER_SENT = "REMINDER_SENT"
    REMINDER_DELIVERED = "REMINDER_DELIVERED"
    REMINDER_FAILED = "REMINDER_FAILED"
    APPOINTMENT_SCHEDULED = "APPOINTMENT_SCHEDULED"
    ANNUAL_VISIT_COMPLETED = "ANNUAL_VISIT_COMPLETED"
    REMINDERS_PAUSED = "REMINDERS_PAUSED"
    PATIENT_OPTED_OUT = "PATIENT_OPTED_OUT"
    SETTINGS_UPDATED = "SETTINGS_UPDATED"
