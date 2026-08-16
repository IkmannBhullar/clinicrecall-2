"""RecallService — the domain core.

**This is the only place in the codebase where patient status or recall dates are decided.**
SPEC §5 is emphatic about that, and the reason is worth stating plainly: the entire product is a
claim about *when* a patient is due. If that logic exists in two places it will disagree with
itself, and the disagreement will show up as a status badge that contradicts the date printed
next to it — in front of a clinic owner.

The module has two halves.

**The pure half** — ``compute_status`` and ``next_annual_due_date`` — are ordinary functions over
plain data. They take ``today`` as an argument and never call ``date.today()`` themselves. That
single decision is what makes the whole thing testable: proving the behaviour at both edges of
every band is a matter of passing different dates, not of mocking the clock.

**The service half** — ``RecallService`` — reads settings, applies the pure functions, writes the
cached ``patients.status`` column, and records what happened. It is the *only* writer of that
column.

Status is a cache, not a field
------------------------------
``patients.status`` is a denormalised copy of ``compute_status(...)``. Caching it means listing
and filtering thousands of patients is an indexed query instead of a computation per row. The
price of a cache is drift, so:

* every mutation path recomputes,
* ``recompute_organization`` re-derives everything (called on startup and by the reminder job,
  so a demo left running overnight corrects itself), and
* a test asserts ``stored_status == compute_status(...)`` for every patient — the drift guard
  SPEC §5.1 requires.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.models.clinic_settings import ClinicSettings
from app.models.enums import ActivityEventType, PatientStatus
from app.models.patient import Patient
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.patients import PatientRepository

# ---------------------------------------------------------------------------------------------
# Band boundaries (SPEC §5.2)
#
# Named constants rather than literals scattered through the comparisons, so the bands can be
# read off in one place and so the tests can assert against the same numbers the code uses.
# ---------------------------------------------------------------------------------------------

#: A visit is shown as recently COMPLETED for this many days, then decays to ACTIVE on its own.
COMPLETED_DISPLAY_WINDOW_DAYS = 30

#: Patients within this many days *before* the due date are DUE_SOON.
DUE_SOON_WINDOW_DAYS = 30

#: Patients up to this many days *past* the due date are still DUE rather than OVERDUE.
DUE_GRACE_DAYS = 7

#: Fallback timezone when a practice has no settings row yet.
DEFAULT_TIMEZONE = "America/Los_Angeles"

#: Fallback recall interval. Only used when no settings row exists; the real value always comes
#: from clinic_settings, because an 18-month recall cycle is a legitimate practice.
DEFAULT_ANNUAL_INTERVAL_MONTHS = 12


# ---------------------------------------------------------------------------------------------
# The pure input
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PatientRecallInput:
    """Everything ``compute_status`` needs, and nothing else.

    Deliberately a plain frozen dataclass rather than the SQLAlchemy ``Patient`` model. Two
    reasons:

    * A test can construct one in a single line with no database, no session, and no fixtures,
      which is why the status matrix in ``tests/test_recall_status.py`` is exhaustive rather than
      representative.
    * It documents the actual inputs to the decision. Reading this class tells you that status
      depends on exactly five things — and that a patient's name, email, and phone number have no
      bearing on it.
    """

    next_annual_due_date: date
    last_annual_visit_date: date
    scheduled_for: date | None = None
    reminders_enabled: bool = True
    opted_out_at: datetime | None = None


def recall_input_from_patient(patient: Patient) -> PatientRecallInput:
    """Project a database row down to the fields that determine status."""
    return PatientRecallInput(
        next_annual_due_date=patient.next_annual_due_date,
        last_annual_visit_date=patient.last_annual_visit_date,
        scheduled_for=patient.scheduled_for,
        reminders_enabled=patient.reminders_enabled,
        opted_out_at=patient.opted_out_at,
    )


# ---------------------------------------------------------------------------------------------
# The pure functions
# ---------------------------------------------------------------------------------------------


def compute_status(patient: PatientRecallInput, today: date) -> PatientStatus:
    """Decide a patient's recall status. Pure, total, and the sole authority (SPEC §5.1).

    ``today`` is a parameter rather than something this function looks up. That is the whole
    reason the status bands can be tested at both edges: there is no clock to freeze and no
    timezone to stub.

    The bands are checked in the order SPEC §5.2 lists them, and the order matters for the first
    three — a patient can satisfy several at once.

    :param patient: the five fields status depends on
    :param today:   the practice's local calendar date (see :func:`today_for_timezone`)
    """
    # ---- 1. INACTIVE -------------------------------------------------------------------------
    # Reminders are off, either because staff paused them or because the patient opted out.
    #
    # This is checked first, so it beats every other band. A patient who has asked to stop
    # hearing from the practice is shown as INACTIVE even if their visit is overdue — surfacing
    # them as OVERDUE would invite staff to chase someone who has withdrawn consent, which is
    # exactly the mistake the opt-out exists to prevent.
    if not patient.reminders_enabled or patient.opted_out_at is not None:
        return PatientStatus.INACTIVE

    # ---- 2. SCHEDULED ------------------------------------------------------------------------
    # An appointment is booked, and it has not happened yet.
    #
    # Note the `>= today` rather than a bare null check. A scheduled date that has passed without
    # anyone marking the visit complete means the appointment was missed, so the patient falls
    # through to their date-derived band below. Without this expiry a demo left running would
    # show patients permanently "Scheduled" and the recovery funnel would be a lie (SPEC §5.2).
    if patient.scheduled_for is not None and patient.scheduled_for >= today:
        return PatientStatus.SCHEDULED

    # ---- 3. COMPLETED ------------------------------------------------------------------------
    # Seen within the last 30 days.
    #
    # A transient display state, not a resting one. Completing a visit advances
    # last_annual_visit_date, which pushes next_annual_due_date a year out — so this patient
    # would otherwise immediately read as ACTIVE and staff would get no visible confirmation
    # that their action registered. After 30 days it decays to ACTIVE by itself.
    #
    # Consequence worth knowing: importing a patient seen three weeks ago shows them as
    # COMPLETED. There is no separate `completed_at` column, so "recently seen" is the only
    # available definition — and it happens to match how staff think about it.
    days_since_visit = (today - patient.last_annual_visit_date).days
    if 0 <= days_since_visit <= COMPLETED_DISPLAY_WINDOW_DAYS:
        return PatientStatus.COMPLETED

    # ---- 4-7. The date-derived bands ---------------------------------------------------------
    # d is days until due: positive means the future, negative means overdue.
    # These four are mutually exclusive across the integers, so their order is irrelevant.
    days_until_due = (patient.next_annual_due_date - today).days

    if days_until_due > DUE_SOON_WINDOW_DAYS:
        return PatientStatus.ACTIVE

    if days_until_due >= 1:
        # 1 to 30 days out.
        return PatientStatus.DUE_SOON

    if days_until_due >= -DUE_GRACE_DAYS:
        # Due today, or up to 7 days past. Still "due" rather than "overdue" because a week's
        # slippage is normal and flagging it as overdue would cry wolf.
        return PatientStatus.DUE

    return PatientStatus.OVERDUE


def next_annual_due_date(last_annual_visit_date: date, annual_interval_months: int) -> date:
    """Return the date a patient is next due, given when they were last seen.

    Uses ``dateutil.relativedelta``, never hand-rolled arithmetic (SPEC §3). Adding "12 months"
    is not the same as adding 365 days, and the difference is not academic: 29 February plus
    twelve months has to land on 28 February, and only a calendar-aware library gets that right
    without a special case. ``tests/test_recall_dates.py`` asserts that behaviour explicitly.

    :param last_annual_visit_date: when the patient was last seen
    :param annual_interval_months: the practice's recall cycle, from clinic_settings
    """
    if annual_interval_months <= 0:
        raise ValueError(
            f"annual_interval_months must be positive, got {annual_interval_months}. "
            "A non-positive interval would make every patient permanently overdue."
        )

    return last_annual_visit_date + relativedelta(months=annual_interval_months)


def today_for_timezone(timezone_name: str) -> date:
    """Return the current calendar date in a practice's own timezone (SPEC §5.3).

    Why this is not ``date.today()``: the server may be running in UTC while the practice is in
    California. At 21:00 Pacific it is already tomorrow in UTC, so ``date.today()`` would show
    every patient one day further along than they are — a demo at 9pm would display tomorrow's
    statuses.

    Falls back to the default timezone rather than raising if the stored name is unrecognised. A
    typo in a settings field should not take down every status in the application; showing dates
    in the wrong timezone is bad, showing no dates at all is worse.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)

    return datetime.now(zone).date()


# ---------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------


class RecallService:
    """Applies the recall rules to stored patients.

    Holds a session and the repositories it needs. One instance per request or per job run;
    settings are cached on the instance because a single reminder run recomputes hundreds of
    patients and each one would otherwise re-read the same settings row.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.patients = PatientRepository(session)
        self.settings_repo = ClinicSettingsRepository(session)
        self.activity = ActivityEventRepository(session)

        # organization_id -> settings row. Scoped to this instance, so it cannot outlive a
        # request or a job run.
        #
        # Note this caches the ORM *object*, not its values. SQLAlchemy's identity map means the
        # cached object is the same instance any other code in this session is holding, so an
        # update to the interval or the timezone is visible here immediately — no invalidation
        # needed for the common case.
        self._settings_cache: dict[uuid.UUID, ClinicSettings] = {}

    # -----------------------------------------------------------------------------------------
    # Settings access
    # -----------------------------------------------------------------------------------------

    def _settings(self, organization_id: uuid.UUID) -> ClinicSettings | None:
        cached = self._settings_cache.get(organization_id)
        if cached is not None:
            return cached

        settings = self.settings_repo.get(organization_id)

        # A missing settings row is deliberately NOT cached. Caching the absence would mean that
        # creating settings later in the same request — which is exactly what the seed and the
        # organization-setup path do — would go unnoticed, and every patient would silently be
        # derived against the fallback interval instead of the practice's real one.
        #
        # Re-querying for an organization that has no settings costs one trivial indexed lookup,
        # and it only happens in the narrow window before setup completes.
        if settings is not None:
            self._settings_cache[organization_id] = settings

        return settings

    def forget_settings(self, organization_id: uuid.UUID) -> None:
        """Drop the cached settings row for an organization.

        Rarely needed, given the identity-map behaviour described above. It matters when settings
        are replaced wholesale rather than mutated — for example when ``make demo-reset`` deletes
        and recreates a practice while a long-lived service instance is still in scope.
        """
        self._settings_cache.pop(organization_id, None)

    def today_for_org(self, organization_id: uuid.UUID) -> date:
        """The practice's current calendar date (SPEC §5.3's ``today_for_org``)."""
        settings = self._settings(organization_id)
        timezone_name = settings.timezone if settings is not None else DEFAULT_TIMEZONE
        return today_for_timezone(timezone_name)

    def interval_months_for_org(self, organization_id: uuid.UUID) -> int:
        """The practice's recall cycle length, in months."""
        settings = self._settings(organization_id)
        if settings is None:
            return DEFAULT_ANNUAL_INTERVAL_MONTHS
        return settings.annual_interval_months

    # -----------------------------------------------------------------------------------------
    # Derivation
    # -----------------------------------------------------------------------------------------

    def due_date_for(self, organization_id: uuid.UUID, last_annual_visit_date: date) -> date:
        """Compute a patient's next due date using this practice's interval."""
        return next_annual_due_date(
            last_annual_visit_date, self.interval_months_for_org(organization_id)
        )

    def apply_derived_fields(self, organization_id: uuid.UUID, patient: Patient) -> Patient:
        """Recompute ``next_annual_due_date`` and ``status`` on a patient object.

        The single place both derived columns are written. Any code path that changes a
        patient's recall state — import, completion, scheduling, opt-out — ends here, which is
        what keeps the cache honest.

        Does not commit; the caller owns the transaction.
        """
        today = self.today_for_org(organization_id)

        patient.next_annual_due_date = self.due_date_for(
            organization_id, patient.last_annual_visit_date
        )
        patient.status = compute_status(recall_input_from_patient(patient), today)
        return patient

    def recompute(self, organization_id: uuid.UUID, patient: Patient) -> bool:
        """Refresh a single patient's cached status. Returns True if it changed."""
        previous = patient.status
        self.apply_derived_fields(organization_id, patient)
        return patient.status != previous

    def recompute_organization(self, organization_id: uuid.UUID) -> int:
        """Re-derive every patient's status. Returns how many changed.

        Statuses go stale on their own simply because time passes: a patient who was DUE_SOON
        yesterday may be DUE today with no one having touched the record. This is what corrects
        that, and it is why a demo left running overnight still shows the truth in the morning.

        Called on API startup, at the top of the reminder job, and by the admin demo utility.
        """
        changed = 0
        for patient in self.patients.list_all(organization_id):
            if self.recompute(organization_id, patient):
                changed += 1
        return changed

    # -----------------------------------------------------------------------------------------
    # Recall state transitions
    #
    # Each of these changes something status depends on, so each ends with a recompute. None of
    # them commits — the caller owns the transaction boundary.
    # -----------------------------------------------------------------------------------------

    def mark_scheduled(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        scheduled_for: date,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Record that an appointment has been booked.

        The activity event written here is not just for the feed: its ``created_at`` is the
        timestamp the "appointments recovered" calculation uses. ``scheduled_for`` is the date of
        the appointment, which is a different thing from *when someone marked it as booked*, and
        the revenue figure needs the latter (SPEC §8).
        """
        patient.scheduled_for = scheduled_for
        self.apply_derived_fields(organization_id, patient)

        self.activity.record(
            organization_id,
            event_type=ActivityEventType.APPOINTMENT_SCHEDULED,
            actor_user_id=actor_user_id,
            subject_patient_id=patient.id,
            # Initials and dates only — never names or contact details (SPEC §9).
            payload={
                "patient_initials": patient.initials,
                "scheduled_for": scheduled_for.isoformat(),
            },
        )
        return patient

    def mark_completed(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        *,
        visit_date: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Record that the annual visit happened, and roll the cycle forward.

        This is the step that closes the loop the whole product exists to run:

            last_annual_visit_date  moves to the visit date
            next_annual_due_date    recomputes a year out
            scheduled_for           clears (the appointment has been kept)
            status                  becomes COMPLETED for 30 days, then ACTIVE

        ``visit_date`` defaults to today in the practice's timezone, which is the normal case —
        staff mark the visit as they finish it.
        """
        today = self.today_for_org(organization_id)
        effective_visit_date = visit_date if visit_date is not None else today

        if effective_visit_date > today:
            raise ValueError(
                f"Visit date {effective_visit_date} is in the future "
                f"(today is {today} for this practice). A visit cannot be completed before it "
                "has happened."
            )

        patient.last_annual_visit_date = effective_visit_date
        # The appointment has been kept, so it is no longer pending. Leaving it set would keep
        # the patient in SCHEDULED, which outranks COMPLETED in the precedence order.
        patient.scheduled_for = None

        self.apply_derived_fields(organization_id, patient)

        self.activity.record(
            organization_id,
            event_type=ActivityEventType.ANNUAL_VISIT_COMPLETED,
            actor_user_id=actor_user_id,
            subject_patient_id=patient.id,
            payload={
                "patient_initials": patient.initials,
                "visit_date": effective_visit_date.isoformat(),
                "next_due_date": patient.next_annual_due_date.isoformat(),
            },
        )
        return patient

    def pause_reminders(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Staff-initiated stop. Distinct from the patient opting out — see ``record_opt_out``."""
        patient.reminders_enabled = False
        self.apply_derived_fields(organization_id, patient)

        self.activity.record(
            organization_id,
            event_type=ActivityEventType.REMINDERS_PAUSED,
            actor_user_id=actor_user_id,
            subject_patient_id=patient.id,
            payload={"patient_initials": patient.initials},
        )
        return patient

    def resume_reminders(
        self,
        organization_id: uuid.UUID,
        patient: Patient,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Undo a staff pause.

        Deliberately does **not** clear ``opted_out_at``. A patient who asked to stop receiving
        reminders stays opted out; only they can reverse that, and only through the link in the
        email they were sent. Staff being able to un-opt-out a patient from the UI would make the
        opt-out meaningless.
        """
        patient.reminders_enabled = True
        self.apply_derived_fields(organization_id, patient)
        return patient

    def record_opt_out(
        self, organization_id: uuid.UUID, patient: Patient, *, opted_out_at: datetime
    ) -> Patient:
        """Patient-initiated stop, via the unsubscribe link in a reminder (SPEC §6.5).

        Recorded as a timestamp rather than a flag because *when* consent was withdrawn is the
        part that matters if it is ever questioned.

        The actor is deliberately ``None``: the patient did this, not a staff member.
        """
        patient.opted_out_at = opted_out_at
        self.apply_derived_fields(organization_id, patient)

        self.activity.record(
            organization_id,
            event_type=ActivityEventType.PATIENT_OPTED_OUT,
            actor_user_id=None,
            subject_patient_id=patient.id,
            payload={
                "patient_initials": patient.initials,
                "opted_out_at": opted_out_at.isoformat(),
            },
        )
        return patient

    # -----------------------------------------------------------------------------------------
    # Drift detection
    # -----------------------------------------------------------------------------------------

    def find_drifted_patients(
        self, organization_id: uuid.UUID
    ) -> list[tuple[Patient, PatientStatus]]:
        """Return patients whose stored status disagrees with a fresh computation.

        Pairs each drifted patient with what its status *should* be. Read-only — nothing is
        corrected here, because a diagnostic that silently repairs what it is measuring cannot
        be used to detect a bug.

        This backs the drift guard test SPEC §5.1 requires, and gives the admin demo utility
        something honest to report.
        """
        today = self.today_for_org(organization_id)
        drifted: list[tuple[Patient, PatientStatus]] = []

        for patient in self.patients.list_all(organization_id):
            expected = compute_status(recall_input_from_patient(patient), today)
            if patient.status != expected:
                drifted.append((patient, expected))

        return drifted


__all__ = [
    "COMPLETED_DISPLAY_WINDOW_DAYS",
    "DUE_GRACE_DAYS",
    "DUE_SOON_WINDOW_DAYS",
    "PatientRecallInput",
    "RecallService",
    "compute_status",
    "next_annual_due_date",
    "recall_input_from_patient",
    "today_for_timezone",
]
