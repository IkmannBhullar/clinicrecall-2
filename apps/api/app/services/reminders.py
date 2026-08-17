"""ReminderService — deciding who gets a reminder, and sending it exactly once.

Two things in this module carry real weight.

**Eligibility (SPEC §6.1)** decides who is contacted. Getting it wrong in one direction means a
practice loses the revenue the product exists to recover; in the other, it means emailing someone
who asked to be left alone.

**Idempotency (SPEC §6.2)** is why the reminder job can be run twice with no consequence. The
spec is explicit that this must *not* be an application-level "have I already sent this?" query,
and the reason is worth stating precisely: two job runs can both execute that query, both read
"no", and both send. The check and the send are not atomic, so no amount of care in the query
fixes it.

Instead the database arbitrates. There is a unique index on
``(patient_id, reminder_rule_id, due_date_snapshot)``; the job inserts the row **first**, inside
the transaction, and a duplicate surfaces as an ``IntegrityError`` from Postgres. Only once the
insert succeeds is anything handed to the email provider. Whatever the timing, exactly one
process wins the insert and exactly one email is sent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.email.provider import EmailProvider, get_email_provider
from app.email.renderer import render_reminder
from app.models.clinic_settings import ClinicSettings
from app.models.enums import (
    ActivityEventType,
    PatientStatus,
    ReminderChannel,
    ReminderEventStatus,
    ReminderSource,
)
from app.models.patient import Patient
from app.models.reminder_event import ReminderEvent
from app.models.reminder_rule import ReminderRule
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.services.recall import RecallService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------------------------

#: How many days late the job may still fire a rule (SPEC §6.1's "bounded catch-up window").
#:
#: A rule fires when today is exactly the target date. If the job does not run that day — the
#: machine was off, the scheduler failed, someone was on holiday — that reminder would otherwise
#: be silently lost forever, since the target date never comes round again.
#:
#: Three days is a deliberate compromise. Long enough to survive a weekend outage; short enough
#: that a reminder never arrives so late it is confusing. It also has a specific consequence for
#: the demo: SPEC §7.3 leaves Sarah Johnson's T_ZERO reminder unsent so there is something to do
#: live on stage, and at 24 days overdue she sits well outside this window, so the job will not
#: quietly backfill it.
CATCH_UP_WINDOW_DAYS = 3

#: Minimum gap between manual "Send Reminder" presses for one patient (SPEC §6.2).
#:
#: Manual sends are exempt from the unique index, so nothing else stops a staff member — or a
#: stuck button, or an impatient double-click — from emailing the same person repeatedly. This
#: protects the patient's inbox rather than the server.
MANUAL_SEND_COOLDOWN = timedelta(hours=1)

#: Statuses that make a patient ineligible for a rule-driven reminder (SPEC §6.1).
INELIGIBLE_STATUSES = frozenset(
    {PatientStatus.SCHEDULED, PatientStatus.COMPLETED, PatientStatus.INACTIVE}
)


class ReminderThrottledError(AppError):
    """A manual send was attempted too soon after the previous one."""

    code = "REMINDER_THROTTLED"
    status_code = 429
    message = "A reminder was sent to this patient recently. Please wait before sending another."


@dataclass
class JobSummary:
    """The structured result SPEC §6.3 requires from the job endpoint.

    Every number answers a question someone actually asks when a run looks wrong:
    "did it look at everyone?", "did it decide correctly?", "did it try to send twice?".
    """

    evaluated: int = 0
    """Patient/rule pairs considered."""

    eligible: int = 0
    """Pairs that passed every eligibility check."""

    created: int = 0
    """reminder_events rows successfully inserted."""

    skipped_duplicate: int = 0
    """Insertions the unique index rejected — the idempotency guarantee doing its job."""

    sent: int = 0
    """Messages the provider accepted."""

    failed: int = 0
    """Messages the provider rejected."""

    delivered: int = 0
    """Previously sent messages confirmed delivered during this run."""

    statuses_recomputed: int = 0
    """Patients whose cached status was corrected before evaluation began."""

    errors: list[str] = field(default_factory=list)
    """Unexpected problems, safe to display. Never contains patient details."""


class ReminderService:
    """Evaluates reminder rules and sends reminders."""

    def __init__(self, session: Session, *, provider: EmailProvider | None = None) -> None:
        self.session = session
        self.provider = provider if provider is not None else get_email_provider()

        self.patients = PatientRepository(session)
        self.rules = ReminderRuleRepository(session)
        self.events = ReminderEventRepository(session)
        self.settings_repo = ClinicSettingsRepository(session)
        self.activity = ActivityEventRepository(session)
        self.recall = RecallService(session)

    # -----------------------------------------------------------------------------------------
    # Eligibility (SPEC §6.1)
    # -----------------------------------------------------------------------------------------

    def target_date_for(self, patient: Patient, rule: ReminderRule) -> date:
        """The calendar date this rule is meant to fire on for this patient."""
        return patient.next_annual_due_date + timedelta(days=rule.days_relative_to_due_date)

    def is_eligible(self, patient: Patient, rule: ReminderRule, today: date) -> bool:
        """Whether this rule should fire for this patient today.

        Every condition from SPEC §6.1, in the cheapest-first order so the common rejection
        (wrong day) is reached without touching anything expensive.
        """
        if not rule.enabled:
            return False

        # Staff paused reminders, or the patient opted out. Either is sufficient.
        if not patient.is_contactable:
            return False

        # An appointment is already booked, the visit just happened, or the patient is inactive.
        # Chasing any of these is at best noise and at worst a reason to distrust the product.
        if patient.status in INELIGIBLE_STATUSES:
            return False

        # No address, no reminder. Recorded as ineligible rather than as a failure, because it is
        # a gap in the practice's data rather than a delivery problem.
        if not patient.email or "@" not in patient.email:
            return False

        # The date test, with the catch-up window. Firing exactly on the target date is the
        # normal case; `days_late` between 1 and 3 is the job recovering from a missed run.
        #
        # Note there is deliberately no "and no event exists" query here, even though SPEC §6.1
        # words it that way. That check would race — see the module docstring. The unique index
        # provides the same guarantee without the race, and `_create_event` below is where a
        # duplicate is detected.
        days_late = (today - self.target_date_for(patient, rule)).days
        return 0 <= days_late <= CATCH_UP_WINDOW_DAYS

    # -----------------------------------------------------------------------------------------
    # The job (SPEC §6.3)
    # -----------------------------------------------------------------------------------------

    def process_reminders(self, organization_id: uuid.UUID) -> JobSummary:
        """Evaluate every rule against every patient, and send what is due.

        Safe to run repeatedly. Running it three times in a row produces the same database state
        as running it once, which is the property SPEC §6.2's test checks.
        """
        summary = JobSummary()

        # Statuses first. Eligibility depends on status, and status goes stale simply because the
        # calendar moved — so evaluating against yesterday's cache would skip patients who became
        # DUE overnight, which is precisely who this job exists to reach.
        summary.statuses_recomputed = self.recall.recompute_organization(organization_id)

        today = self.recall.today_for_org(organization_id)
        clinic_settings = self.settings_repo.get(organization_id)
        enabled_rules = list(self.rules.list_enabled(organization_id))

        if not enabled_rules:
            logger.info("No enabled reminder rules for organization %s", organization_id)
            return summary

        for patient in self.patients.list_all(organization_id):
            for rule in enabled_rules:
                summary.evaluated += 1

                if not self.is_eligible(patient, rule, today):
                    continue

                summary.eligible += 1
                self._process_one(organization_id, patient, rule, clinic_settings, summary)

        # Promote anything whose simulated delivery window has elapsed, so a second run of the
        # job advances the earlier run's messages from SENT to DELIVERED.
        summary.delivered = self.confirm_deliveries(organization_id)

        logger.info(
            "Reminder job for organization %s: evaluated=%d eligible=%d created=%d "
            "skipped_duplicate=%d sent=%d failed=%d",
            organization_id,
            summary.evaluated,
            summary.eligible,
            summary.created,
            summary.skipped_duplicate,
            summary.sent,
            summary.failed,
        )
        return summary

    def _process_one(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        rule: ReminderRule,
        clinic_settings: ClinicSettings | None,
        summary: JobSummary,
    ) -> None:
        """Create and send one rule-driven reminder, or skip it as a duplicate.

        THE IDEMPOTENCY MECHANISM (SPEC §6.2), in order:

            1. Insert the reminder_events row and flush it.
            2. If Postgres raises IntegrityError, this reminder already exists — skip. Nothing
               was sent, because nothing has been handed to the provider yet.
            3. Only now, with the insert committed to this transaction, call the provider.

        The order is the whole point. Sending first and recording afterwards would mean a crash
        between the two steps sends the same patient a second email on the next run.
        """
        event = ReminderEvent(
            organization_id=organization_id,
            patient_id=patient.id,
            reminder_rule_id=rule.id,
            source=ReminderSource.RULE,
            due_date_snapshot=patient.next_annual_due_date,
            channel=ReminderChannel.EMAIL,
            status=ReminderEventStatus.SCHEDULED,
            scheduled_at=datetime.now(UTC),
        )

        # A SAVEPOINT. Without it, the IntegrityError below would poison the whole transaction
        # and every later patient in this run would fail too — one duplicate would abort the job.
        # begin_nested lets just this insert roll back while the run continues.
        savepoint = self.session.begin_nested()
        try:
            self.session.add(event)
            self.session.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            summary.skipped_duplicate += 1
            logger.debug(
                "Reminder already exists for patient %s rule %s due %s — skipping",
                patient.public_id,
                rule.key.value,
                patient.next_annual_due_date,
            )
            return

        summary.created += 1
        self._send(organization_id, patient, event, clinic_settings, summary)

    # -----------------------------------------------------------------------------------------
    # Sending
    # -----------------------------------------------------------------------------------------

    def _send(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        event: ReminderEvent,
        clinic_settings: ClinicSettings | None,
        summary: JobSummary,
    ) -> None:
        """Render, hand to the provider, and record the outcome on the event."""
        message = render_reminder(patient, clinic_settings)

        # Stored so the UI can show the exact email that went out (SPEC §6.4), rather than
        # re-rendering from a template that may since have been edited.
        event.rendered_subject = message.subject
        event.rendered_body_html = message.html_body
        event.rendered_body_text = message.text_body
        event.status = ReminderEventStatus.SENDING

        try:
            result = self.provider.send(message)
        except Exception as exc:
            # A provider raising is a fault in the provider, not a bad address. The event is
            # marked failed so the reminder is visible and retryable rather than vanishing.
            logger.exception("Email provider raised while sending reminder %s", event.id)
            event.status = ReminderEventStatus.FAILED
            event.failure_reason = "The email service could not be reached."
            summary.failed += 1
            summary.errors.append(f"Provider error: {type(exc).__name__}")
            self._record_activity(
                organization_id, patient, event, ActivityEventType.REMINDER_FAILED
            )
            return

        event.provider_message_id = result.provider_message_id

        if result.accepted:
            event.status = ReminderEventStatus.SENT
            event.sent_at = datetime.now(UTC)
            summary.sent += 1
            self._record_activity(organization_id, patient, event, ActivityEventType.REMINDER_SENT)
        else:
            event.status = ReminderEventStatus.FAILED
            event.failure_reason = result.failure_reason
            summary.failed += 1
            self._record_activity(
                organization_id, patient, event, ActivityEventType.REMINDER_FAILED
            )

    def _record_activity(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        event: ReminderEvent,
        event_type: ActivityEventType,
    ) -> None:
        """Write the activity-feed entry for a reminder outcome.

        Payload holds initials and enum values only — never a name, an address, or the rendered
        message (SPEC §9).
        """
        payload: dict[str, object] = {
            "patient_initials": patient.initials,
            "channel": event.channel.value,
            "source": event.source.value,
        }
        if event.reminder_rule is not None:
            payload["rule_key"] = event.reminder_rule.key.value
        if event.failure_reason:
            payload["failure_reason"] = event.failure_reason

        self.activity.record(
            organization_id,
            event_type=event_type,
            actor_user_id=None,  # the job acted, not a person
            subject_patient_id=patient.id,
            payload=payload,
        )

    # -----------------------------------------------------------------------------------------
    # Delivery confirmation
    # -----------------------------------------------------------------------------------------

    def confirm_deliveries(self, organization_id: uuid.UUID, *, now: datetime | None = None) -> int:
        """Promote SENT events to DELIVERED once their delivery window has elapsed.

        With a real provider this would be driven by a webhook. The mock reports a delay instead,
        and this sweep applies it — which gives the UI a genuine SENT → DELIVERED transition to
        show rather than a badge that appears in its final state (SPEC §6.4).

        Takes ``now`` explicitly so tests need not sleep.
        """
        current_time = now if now is not None else datetime.now(UTC)
        delay = timedelta(seconds=getattr(self.provider, "delivery_delay_seconds", 0.0))
        confirmed = 0

        for event in self.events.list_recent(organization_id, limit=500):
            if event.status is not ReminderEventStatus.SENT or event.sent_at is None:
                continue
            if current_time - event.sent_at < delay:
                continue

            event.status = ReminderEventStatus.DELIVERED
            event.delivered_at = current_time
            confirmed += 1

            self.activity.record(
                organization_id,
                event_type=ActivityEventType.REMINDER_DELIVERED,
                actor_user_id=None,
                subject_patient_id=event.patient_id,
                payload={"channel": event.channel.value},
            )

        return confirmed

    # -----------------------------------------------------------------------------------------
    # Manual send (SPEC §6.2's carve-out)
    # -----------------------------------------------------------------------------------------

    def send_manual_reminder(
        self,
        organization_id: uuid.UUID,
        public_id: str,
        *,
        actor_user_id: uuid.UUID | None = None,
        source: ReminderSource = ReminderSource.MANUAL,
    ) -> ReminderEvent:
        """Send a reminder because a staff member asked, not because a rule fired.

        The event carries ``reminder_rule_id = NULL``, and Postgres does not treat two NULLs as
        equal in a unique index — so this can never collide with a rule-driven reminder. That
        carve-out exists for a specific moment: pressing "Send Reminder" during a demo, on a
        patient whose rule has already fired, must not produce a duplicate-key error on stage.

        Protection against repeats is a time-based cooldown instead, which is the right shape of
        control here since it is a patient's inbox being protected rather than a database
        constraint.
        """
        patient = self.patients.get_by_public_id(organization_id, public_id)
        if patient is None:
            raise NotFoundError("That patient could not be found.")

        if not patient.is_contactable:
            raise ConflictError(
                "Reminders are turned off for this patient. Resume them before sending."
            )

        if not patient.email or "@" not in patient.email:
            raise ConflictError("This patient has no valid email address on file.")

        self._enforce_manual_cooldown(organization_id, patient)

        event = ReminderEvent(
            organization_id=organization_id,
            patient_id=patient.id,
            reminder_rule_id=None,  # the carve-out
            source=source,
            due_date_snapshot=patient.next_annual_due_date,
            channel=ReminderChannel.EMAIL,
            status=ReminderEventStatus.SCHEDULED,
            scheduled_at=datetime.now(UTC),
        )
        self.session.add(event)
        self.session.flush()

        summary = JobSummary()
        self._send(
            organization_id,
            patient,
            event,
            self.settings_repo.get(organization_id),
            summary,
        )

        # Attribute it to the person who pressed the button, rather than to the system.
        if actor_user_id is not None:
            for activity in self.activity.list_for_patient(organization_id, patient.id, limit=1):
                activity.actor_user_id = actor_user_id

        return event

    def _enforce_manual_cooldown(self, organization_id: uuid.UUID, patient: Patient) -> None:
        """Refuse a manual send that follows too closely on the previous one."""
        recent = self.events.list_for_patient(organization_id, patient.id, limit=10)
        cutoff = datetime.now(UTC) - MANUAL_SEND_COOLDOWN

        for event in recent:
            if event.source is not ReminderSource.MANUAL:
                continue
            if event.created_at and event.created_at > cutoff:
                raise ReminderThrottledError()

    # -----------------------------------------------------------------------------------------
    # Opt-out (SPEC §6.5)
    # -----------------------------------------------------------------------------------------

    def record_opt_out_by_public_id(self, public_id: str) -> bool:
        """Opt a patient out, from the tokenized link in a reminder email.

        Unscoped by organization, and that is not an oversight. The caller is a patient clicking a
        link in their inbox — they have no session, no organization, and nothing to scope by. The
        signed token is what proves the request is legitimate, and it is verified before this is
        called.

        Idempotent: clicking the link twice is not an error. A patient who is unsure whether it
        worked and clicks again should see the same confirmation.
        """
        patient = self.session.query(Patient).filter(Patient.public_id == public_id).one_or_none()
        if patient is None:
            return False

        if patient.opted_out_at is not None:
            return True  # already opted out; nothing to do, and no second activity event

        self.recall.record_opt_out(patient.organization_id, patient, opted_out_at=datetime.now(UTC))
        return True
