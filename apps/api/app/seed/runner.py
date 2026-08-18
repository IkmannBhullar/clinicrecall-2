"""Builds the demo database from the fixtures (SPEC §7.3).

**Idempotency, and how it is achieved.** Every seeded row gets a UUID derived from its natural key
rather than a random one, so re-running the seed finds the existing row and updates it instead of
inserting a duplicate. That is checked by ``scripts/check-seed-idempotent.sh``, which runs the
seed twice and asserts the row counts do not move.

Deterministic ids buy a second thing worth having: a patient keeps the same URL across a
``make demo-reset``, so a screenshot, a bookmark, or a Playwright test written last week still
points at Sarah Johnson.

**Everything is relative to today.** Not one date in the demo is hardcoded (SPEC constraint D1).
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.email.provider import MockEmailProvider
from app.email.renderer import render_reminder
from app.models.activity_event import ActivityEvent
from app.models.clinic_settings import ClinicSettings
from app.models.enums import (
    ActivityEventType,
    PatientStatus,
    ReminderChannel,
    ReminderEventStatus,
    ReminderRuleKey,
    ReminderSource,
)
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.reminder_event import ReminderEvent
from app.models.user import User
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.seed import fixtures as fx
from app.seed.supabase_users import SupabaseAdminError, ensure_auth_user, supabase_is_reachable
from app.services.recall import RecallService, today_for_timezone

logger = logging.getLogger(__name__)


@dataclass
class SeedSummary:
    """What the seed produced. Printed by the CLI and asserted by the idempotency check."""

    organizations: int = 0
    users: int = 0
    patients: int = 0
    reminder_rules: int = 0
    reminder_events: int = 0
    activity_events: int = 0
    auth_accounts: int = 0
    auth_skipped: bool = False


class SeedRunner:
    """Creates (or refreshes) the Green Valley demo data."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.recall = RecallService(session)
        # A zero-delay provider: seeded reminders are historical, so their delivery already
        # happened and there is nothing to wait for.
        self.provider = MockEmailProvider(delivery_delay_seconds=0.0)

    # -----------------------------------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------------------------------

    def run(self, *, create_auth_accounts: bool = True) -> SeedSummary:
        summary = SeedSummary()

        organization = self._ensure_organization()
        summary.organizations = 1

        settings_row = self._ensure_settings(organization)
        today = today_for_timezone(settings_row.timezone)

        rules = self._ensure_rules(organization)
        summary.reminder_rules = len(rules)

        summary.users, summary.auth_accounts, summary.auth_skipped = self._ensure_users(
            organization, create_auth_accounts=create_auth_accounts
        )
        admin = self.session.get(User, fx.seed_uuid("user", fx.DEMO_ADMIN.key))

        roster = self._build_roster(today)
        rules_by_key = {rule.key: rule for rule in rules}

        for entry in roster:
            patient = self._ensure_patient(organization, entry, today)
            summary.patients += 1
            summary.reminder_events += self._ensure_reminders(
                organization, patient, entry, rules_by_key, settings_row, today
            )
            summary.activity_events += self._ensure_patient_activity(
                organization, patient, entry, admin, today
            )

        summary.activity_events += self._ensure_organization_activity(organization, admin, today)

        # Statuses last, so every patient's cached value reflects the dates just written. Without
        # this the drift guard would fail on a freshly seeded database — which is exactly the
        # kind of inconsistency it exists to catch.
        self.recall.recompute_organization(organization.id)

        return summary

    # -----------------------------------------------------------------------------------------
    # Organization, settings, rules
    # -----------------------------------------------------------------------------------------

    def _ensure_organization(self) -> Organization:
        organization_id = fx.seed_uuid("organization", fx.ORGANIZATION_SLUG)
        organization = self.session.get(Organization, organization_id)

        if organization is None:
            organization = Organization(
                id=organization_id, name=fx.ORGANIZATION_NAME, slug=fx.ORGANIZATION_SLUG
            )
            self.session.add(organization)
            self.session.flush()

        return organization

    def _ensure_settings(self, organization: Organization) -> ClinicSettings:
        settings_row = ClinicSettingsRepository(self.session).get_or_create(
            organization.id,
            clinic_name=fx.ORGANIZATION_NAME,
            timezone=fx.CLINIC_TIMEZONE,
        )

        # Refreshed on every run so a value changed during a demo returns to the documented one.
        settings_row.phone = fx.CLINIC_PHONE
        settings_row.email = fx.CLINIC_EMAIL
        settings_row.website = fx.CLINIC_WEBSITE
        settings_row.scheduling_url = fx.CLINIC_SCHEDULING_URL
        settings_row.reminder_signature = fx.CLINIC_SIGNATURE
        settings_row.estimated_annual_visit_value = Decimal(fx.ESTIMATED_VISIT_VALUE)
        self.session.flush()

        return settings_row

    def _ensure_rules(self, organization: Organization) -> list:  # type: ignore[type-arg]
        rules = ReminderRuleRepository(self.session).create_default_rules(organization.id)
        self.session.flush()
        return rules

    # -----------------------------------------------------------------------------------------
    # Staff
    # -----------------------------------------------------------------------------------------

    def _ensure_users(
        self, organization: Organization, *, create_auth_accounts: bool
    ) -> tuple[int, int, bool]:
        """Create the application users, and their Supabase accounts if reachable.

        Returns ``(users, auth_accounts, auth_skipped)``.

        Auth failures do not stop the seed. A test environment with no Supabase should still get
        its patient data — refusing to seed 55 patients because one HTTP call failed would make
        the whole suite depend on the auth service being up.
        """
        auth_available = create_auth_accounts and supabase_is_reachable()
        auth_created = 0

        for seed_user in fx.SEED_USERS:
            user_id = fx.seed_uuid("user", seed_user.key)
            user = self.session.get(User, user_id)

            auth_user_id: uuid.UUID | None = None
            if auth_available:
                try:
                    auth_user_id = ensure_auth_user(seed_user.email, seed_user.password)
                    auth_created += 1
                except SupabaseAdminError as exc:
                    logger.warning("Could not create demo account %s: %s", seed_user.email, exc)

            if user is None:
                user = User(
                    id=user_id,
                    organization_id=organization.id,
                    # A deterministic placeholder when auth is unavailable. It cannot be used to
                    # sign in — nothing in Supabase matches it — which is the correct outcome:
                    # the application user exists, the login does not.
                    auth_user_id=auth_user_id or fx.seed_uuid("auth", seed_user.key),
                    first_name=seed_user.first_name,
                    last_name=seed_user.last_name,
                    email=seed_user.email,
                    role=seed_user.role,
                )
                self.session.add(user)
            elif auth_user_id is not None:
                # Relink. Supabase may have been reset independently of the application database,
                # in which case the stored id points at an account that no longer exists and the
                # demo login silently stops working.
                user.auth_user_id = auth_user_id

            self.session.flush()

        return len(fx.SEED_USERS), auth_created, not auth_available

    # -----------------------------------------------------------------------------------------
    # The roster
    # -----------------------------------------------------------------------------------------

    def _build_roster(self, today: date) -> list[fx.RosterEntry]:
        """The full 55: the six named fixtures, plus generated patients filling the distribution.

        Seeded ``random`` so the roster is identical on every run — a demo where the patient list
        reshuffles each time it is reset is one where no screenshot stays accurate.
        """
        # A fixed seed, so the generated roster is byte-identical on every run.
        rng = random.Random(20260817)
        roster: list[fx.RosterEntry] = []

        # 1. The named fixtures, exactly as declared.
        for named in fx.NAMED_FIXTURES:
            roster.append(
                fx.RosterEntry(
                    key=named.key,
                    first_name=named.first_name,
                    last_name=named.last_name,
                    email=named.email,
                    phone=named.phone,
                    days_until_due=named.days_until_due,
                    expected_status=named.expected_status,
                    reminders=list(named.reminders),
                    scheduled_in_days=named.scheduled_in_days,
                )
            )

        # 2. Work out how many more of each status are needed, after the named ones.
        needed = dict(fx.STATUS_DISTRIBUTION)
        for named in fx.NAMED_FIXTURES:
            needed[named.expected_status] -= 1

        index = 0
        for status, count in needed.items():
            low, high = fx.STATUS_DUE_RANGES[status]

            for _ in range(max(count, 0)):
                first = fx.FIRST_NAMES[index % len(fx.FIRST_NAMES)]
                last = fx.LAST_NAMES[(index * 7 + 3) % len(fx.LAST_NAMES)]
                key = f"roster-{index:03d}"
                days_until_due = rng.randint(low, high)

                entry = fx.RosterEntry(
                    key=key,
                    first_name=first,
                    last_name=last,
                    email=f"{first.lower()}.{last.lower()}.{index}@example.com",
                    phone=f"555-{rng.randint(200, 899):03d}{rng.randint(0, 9)}",
                    days_until_due=days_until_due,
                    expected_status=status,
                )

                if status is PatientStatus.SCHEDULED:
                    entry.scheduled_in_days = rng.randint(2, 25)
                    # A delivered reminder first, so these count towards "appointments recovered"
                    # — the metric requires exactly that sequence (SPEC §8).
                    entry.reminders = [
                        fx.SeedReminder(ReminderRuleKey.T_MINUS_7, days_ago=rng.randint(4, 25))
                    ]
                elif status is PatientStatus.INACTIVE:
                    # One paused by staff, one opted out — the two distinct routes to INACTIVE.
                    if index % 2 == 0:
                        entry.reminders_paused = True
                    else:
                        entry.opted_out = True
                else:
                    entry.reminders = self._history_for(status, days_until_due, rng)

                roster.append(entry)
                index += 1

        return roster

    @staticmethod
    def _history_for(
        status: PatientStatus, days_until_due: int, rng: random.Random
    ) -> list[fx.SeedReminder]:
        """Plausible past reminders for a generated patient.

        Only rules whose target date has already passed *and* falls inside the history window
        (SPEC §7.3's 60-90 days) produce an event — so the seeded history is consistent with what
        the reminder job would actually have done, rather than decorative.
        """
        offsets = {
            ReminderRuleKey.T_MINUS_30: -30,
            ReminderRuleKey.T_MINUS_7: -7,
            ReminderRuleKey.T_ZERO: 0,
            ReminderRuleKey.T_PLUS_30: 30,
        }
        history: list[fx.SeedReminder] = []

        for rule_key, offset in offsets.items():
            days_ago = -(days_until_due + offset)
            if 0 < days_ago <= fx.HISTORY_WINDOW_DAYS:
                # A small share of real sends bounce. Keeping a few in the seeded history means
                # the delivery-performance strip shows a realistic mix rather than a perfect
                # record, which nobody believes.
                failed = rng.random() < 0.04
                history.append(
                    fx.SeedReminder(
                        rule_key,
                        days_ago=days_ago,
                        status=(
                            ReminderEventStatus.FAILED if failed else ReminderEventStatus.DELIVERED
                        ),
                        failure_reason=(
                            "Recipient address does not exist (hard bounce)." if failed else None
                        ),
                    )
                )

        return history

    # -----------------------------------------------------------------------------------------
    # Patients
    # -----------------------------------------------------------------------------------------

    def _ensure_patient(
        self, organization: Organization, entry: fx.RosterEntry, today: date
    ) -> Patient:
        patient_id = fx.seed_uuid("patient", entry.key)
        patient = self.session.get(Patient, patient_id)

        due_date = today + timedelta(days=entry.days_until_due)
        # Work backwards: the last visit is one cycle before the due date.
        last_visit = due_date - timedelta(days=365)

        if patient is None:
            patient = Patient(
                id=patient_id,
                organization_id=organization.id,
                public_id=fx.seed_public_id(entry.key),
                external_id=f"MRN-{fx.seed_uuid('mrn', entry.key).int % 900000 + 100000}",
                first_name=entry.first_name,
                last_name=entry.last_name,
                email=entry.email,
                phone=entry.phone,
                last_annual_visit_date=last_visit,
                next_annual_due_date=due_date,
            )
            self.session.add(patient)
        else:
            patient.first_name = entry.first_name
            patient.last_name = entry.last_name
            patient.email = entry.email
            patient.phone = entry.phone
            patient.last_annual_visit_date = last_visit

        patient.scheduled_for = (
            today + timedelta(days=entry.scheduled_in_days)
            if entry.scheduled_in_days is not None
            else None
        )
        patient.reminders_enabled = not entry.reminders_paused
        patient.opted_out_at = datetime.now(UTC) - timedelta(days=12) if entry.opted_out else None

        # Derived fields through RecallService, never written by hand (SPEC §5.1).
        self.recall.apply_derived_fields(organization.id, patient)
        self.session.flush()

        return patient

    # -----------------------------------------------------------------------------------------
    # Reminder history
    # -----------------------------------------------------------------------------------------

    def _ensure_reminders(
        self,
        organization: Organization,
        patient: Patient,
        entry: fx.RosterEntry,
        rules_by_key: dict[ReminderRuleKey, object],
        settings_row: ClinicSettings,
        today: date,
    ) -> int:
        created = 0

        for reminder in entry.reminders:
            event_id = fx.seed_uuid(
                "reminder", entry.key, str(reminder.rule_key), str(reminder.days_ago)
            )
            if self.session.get(ReminderEvent, event_id) is not None:
                created += 1
                continue

            rule = rules_by_key.get(reminder.rule_key) if reminder.rule_key else None
            sent_at = datetime.combine(
                today - timedelta(days=reminder.days_ago),
                # A believable hour rather than midnight. The demo shows "delivered this morning"
                # next to Jennifer Tran, and 00:00 would read as machine-generated.
                time(hour=9, minute=14),
                tzinfo=UTC,
            )

            # The message is rendered and stored, exactly as a live send would (SPEC §6.4), so
            # opening a historical reminder in the UI shows a real email rather than a blank
            # panel.
            message = render_reminder(patient, settings_row)
            delivered = reminder.status is ReminderEventStatus.DELIVERED

            self.session.add(
                ReminderEvent(
                    id=event_id,
                    organization_id=organization.id,
                    patient_id=patient.id,
                    reminder_rule_id=getattr(rule, "id", None),
                    source=ReminderSource.RULE if rule else ReminderSource.MANUAL,
                    # The due date this reminder was computed against — part of the idempotency
                    # key, so a seeded event blocks the live job from re-sending it.
                    due_date_snapshot=patient.next_annual_due_date,
                    channel=ReminderChannel.EMAIL,
                    status=reminder.status,
                    scheduled_at=sent_at,
                    sent_at=sent_at,
                    delivered_at=sent_at + timedelta(seconds=8) if delivered else None,
                    provider_message_id=f"mock-seed-{event_id.hex[:12]}",
                    failure_reason=reminder.failure_reason,
                    rendered_subject=message.subject,
                    rendered_body_html=message.html_body,
                    rendered_body_text=message.text_body,
                    created_at=sent_at,
                )
            )
            created += 1

        self.session.flush()
        return created

    # -----------------------------------------------------------------------------------------
    # Activity feed
    # -----------------------------------------------------------------------------------------

    def _activity(
        self,
        organization: Organization,
        key: str,
        *,
        event_type: ActivityEventType,
        occurred_at: datetime,
        actor_user_id: uuid.UUID | None = None,
        patient_id: uuid.UUID | None = None,
        payload: dict[str, object] | None = None,
    ) -> int:
        """Insert one activity event, keyed deterministically so re-seeding does not duplicate."""
        event_id = fx.seed_uuid("activity", key)
        if self.session.get(ActivityEvent, event_id) is not None:
            return 1

        self.session.add(
            ActivityEvent(
                id=event_id,
                organization_id=organization.id,
                actor_user_id=actor_user_id,
                type=event_type,
                subject_patient_id=patient_id,
                payload=payload or {},
                created_at=occurred_at,
            )
        )
        return 1

    def _ensure_patient_activity(
        self,
        organization: Organization,
        patient: Patient,
        entry: fx.RosterEntry,
        admin: User | None,
        today: date,
    ) -> int:
        """Activity entries mirroring what happened to this patient.

        Payloads carry initials and dates only, never names or addresses (SPEC §9).
        """
        count = 0
        actor_id = admin.id if admin is not None else None

        for reminder in entry.reminders:
            occurred = datetime.combine(
                today - timedelta(days=reminder.days_ago), time(hour=9, minute=14), tzinfo=UTC
            )
            delivered = reminder.status is ReminderEventStatus.DELIVERED

            count += self._activity(
                organization,
                f"{entry.key}-reminder-{reminder.rule_key}-{reminder.days_ago}",
                event_type=(
                    ActivityEventType.REMINDER_DELIVERED
                    if delivered
                    else ActivityEventType.REMINDER_FAILED
                ),
                occurred_at=occurred,
                patient_id=patient.id,
                payload={
                    "patient_initials": patient.initials,
                    "channel": "EMAIL",
                    "rule_key": reminder.rule_key.value if reminder.rule_key else "MANUAL",
                },
            )

        if entry.scheduled_in_days is not None:
            # Dated a couple of days after the reminder, so it satisfies the "booked within 30
            # days of a delivered reminder" rule the revenue figure depends on (SPEC §8).
            booked_days_ago = (
                min((reminder.days_ago for reminder in entry.reminders), default=6) - 2
            )
            count += self._activity(
                organization,
                f"{entry.key}-scheduled",
                event_type=ActivityEventType.APPOINTMENT_SCHEDULED,
                occurred_at=datetime.combine(
                    today - timedelta(days=max(booked_days_ago, 1)),
                    time(hour=11, minute=2),
                    tzinfo=UTC,
                ),
                actor_user_id=actor_id,
                patient_id=patient.id,
                payload={
                    "patient_initials": patient.initials,
                    "scheduled_for": patient.scheduled_for.isoformat()
                    if patient.scheduled_for
                    else None,
                },
            )

        if entry.expected_status is PatientStatus.COMPLETED:
            count += self._activity(
                organization,
                f"{entry.key}-completed",
                event_type=ActivityEventType.ANNUAL_VISIT_COMPLETED,
                occurred_at=datetime.combine(
                    patient.last_annual_visit_date, time(hour=15, minute=30), tzinfo=UTC
                ),
                actor_user_id=actor_id,
                patient_id=patient.id,
                payload={
                    "patient_initials": patient.initials,
                    "visit_date": patient.last_annual_visit_date.isoformat(),
                },
            )

        if entry.opted_out:
            count += self._activity(
                organization,
                f"{entry.key}-opted-out",
                event_type=ActivityEventType.PATIENT_OPTED_OUT,
                occurred_at=datetime.now(UTC) - timedelta(days=12),
                # The patient did this, not a member of staff.
                actor_user_id=None,
                patient_id=patient.id,
                payload={"patient_initials": patient.initials},
            )

        if entry.reminders_paused:
            count += self._activity(
                organization,
                f"{entry.key}-paused",
                event_type=ActivityEventType.REMINDERS_PAUSED,
                occurred_at=datetime.now(UTC) - timedelta(days=20),
                actor_user_id=actor_id,
                patient_id=patient.id,
                payload={"patient_initials": patient.initials},
            )

        return count

    def _ensure_organization_activity(
        self, organization: Organization, admin: User | None, today: date
    ) -> int:
        """The practice-wide entries: the original import, and a settings change.

        Without these the Activity feed's Imports filter is empty at first login, which makes a
        working filter look broken.
        """
        actor_id = admin.id if admin is not None else None
        count = 0

        count += self._activity(
            organization,
            "initial-import",
            event_type=ActivityEventType.PATIENT_IMPORTED,
            occurred_at=datetime.combine(
                today - timedelta(days=fx.HISTORY_WINDOW_DAYS),
                time(hour=10, minute=5),
                tzinfo=UTC,
            ),
            actor_user_id=actor_id,
            payload={
                "total_rows": fx.TOTAL_PATIENTS,
                "created": fx.TOTAL_PATIENTS,
                "updated": 0,
                "skipped": 0,
            },
        )

        count += self._activity(
            organization,
            "settings-configured",
            event_type=ActivityEventType.SETTINGS_UPDATED,
            occurred_at=datetime.combine(
                today - timedelta(days=fx.HISTORY_WINDOW_DAYS - 1),
                time(hour=10, minute=22),
                tzinfo=UTC,
            ),
            actor_user_id=actor_id,
            payload={"fields": ["estimated_annual_visit_value", "reminder_signature"]},
        )

        return count
