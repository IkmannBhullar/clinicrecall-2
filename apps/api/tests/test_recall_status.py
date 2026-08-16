"""Status bands (SPEC §5.2, §5.4).

SPEC §5.4 requires coverage of "each status band including both edges of every band". That is not
pedantry — off-by-one errors at a boundary are the characteristic bug in this kind of logic, and
they are invisible in casual testing because the middle of every band behaves correctly. A
patient exactly 30 days out, or exactly 7 days overdue, is where a wrong comparison operator
shows itself.

So every band below is tested at both of its edges *and* one step outside each edge, which is the
only way to prove a boundary sits where it is supposed to.

None of these tests touch the database. ``compute_status`` is a pure function that takes ``today``
as an argument, so the entire matrix is a table of dates — no clock to freeze, no fixtures, and
no ambiguity about what is being asserted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from app.models.enums import PatientStatus
from app.services.recall import (
    COMPLETED_DISPLAY_WINDOW_DAYS,
    DUE_GRACE_DAYS,
    DUE_SOON_WINDOW_DAYS,
    PatientRecallInput,
    compute_status,
)

# A fixed reference date. Using a literal rather than the real today makes every expectation in
# this file arithmetic anyone can check by hand, and the function under test has no hidden clock
# to disagree with it.
TODAY = date(2026, 6, 15)


def patient_due_in(
    days: int,
    *,
    last_visit_days_ago: int = 365,
    scheduled_for: date | None = None,
    reminders_enabled: bool = True,
    opted_out_at: datetime | None = None,
) -> PatientRecallInput:
    """Build a recall input whose due date is ``days`` from TODAY.

    ``last_visit_days_ago`` defaults to a year, which is far enough back that the COMPLETED band
    never fires by accident. Tests that care about COMPLETED set it explicitly.
    """
    return PatientRecallInput(
        next_annual_due_date=TODAY + timedelta(days=days),
        last_annual_visit_date=TODAY - timedelta(days=last_visit_days_ago),
        scheduled_for=scheduled_for,
        reminders_enabled=reminders_enabled,
        opted_out_at=opted_out_at,
    )


# ---------------------------------------------------------------------------------------------
# ACTIVE — d > 30
# ---------------------------------------------------------------------------------------------


def test_active_at_its_lower_edge() -> None:
    """31 days out is the first day of ACTIVE."""
    assert compute_status(patient_due_in(DUE_SOON_WINDOW_DAYS + 1), TODAY) is PatientStatus.ACTIVE


def test_one_day_below_the_active_edge_is_due_soon() -> None:
    """30 days out is the last day of DUE_SOON. This is the assertion that catches `>=` vs `>`."""
    assert compute_status(patient_due_in(DUE_SOON_WINDOW_DAYS), TODAY) is PatientStatus.DUE_SOON


@pytest.mark.parametrize("days", [31, 60, 180, 364, 365])
def test_active_across_its_range(days: int) -> None:
    assert compute_status(patient_due_in(days), TODAY) is PatientStatus.ACTIVE


# ---------------------------------------------------------------------------------------------
# DUE_SOON — 1 <= d <= 30
# ---------------------------------------------------------------------------------------------


def test_due_soon_at_its_upper_edge() -> None:
    assert compute_status(patient_due_in(30), TODAY) is PatientStatus.DUE_SOON


def test_due_soon_at_its_lower_edge() -> None:
    """One day out is still DUE_SOON; zero days out is DUE."""
    assert compute_status(patient_due_in(1), TODAY) is PatientStatus.DUE_SOON


def test_one_day_below_the_due_soon_edge_is_due() -> None:
    assert compute_status(patient_due_in(0), TODAY) is PatientStatus.DUE


@pytest.mark.parametrize("days", [1, 5, 11, 20, 29, 30])
def test_due_soon_across_its_range(days: int) -> None:
    assert compute_status(patient_due_in(days), TODAY) is PatientStatus.DUE_SOON


# ---------------------------------------------------------------------------------------------
# DUE — -7 <= d <= 0
# ---------------------------------------------------------------------------------------------


def test_due_on_the_day_itself() -> None:
    assert compute_status(patient_due_in(0), TODAY) is PatientStatus.DUE


def test_due_at_its_lower_edge() -> None:
    """Exactly 7 days past due is the last day of the grace period."""
    assert compute_status(patient_due_in(-DUE_GRACE_DAYS), TODAY) is PatientStatus.DUE


def test_one_day_past_the_due_edge_is_overdue() -> None:
    """8 days past due tips into OVERDUE. The other half of the `>=` vs `>` check."""
    assert compute_status(patient_due_in(-DUE_GRACE_DAYS - 1), TODAY) is PatientStatus.OVERDUE


@pytest.mark.parametrize("days", [0, -1, -3, -6, -7])
def test_due_across_its_range(days: int) -> None:
    assert compute_status(patient_due_in(days), TODAY) is PatientStatus.DUE


# ---------------------------------------------------------------------------------------------
# OVERDUE — d < -7
# ---------------------------------------------------------------------------------------------


def test_overdue_at_its_upper_edge() -> None:
    assert compute_status(patient_due_in(-8), TODAY) is PatientStatus.OVERDUE


@pytest.mark.parametrize("days", [-8, -14, -24, -90, -400])
def test_overdue_across_its_range(days: int) -> None:
    assert compute_status(patient_due_in(days), TODAY) is PatientStatus.OVERDUE


def test_sarah_johnson_is_overdue() -> None:
    """The demo's opening patient: due ~24 days ago (SPEC §7.3).

    Pinned here as well as in the seed test, so that a change to the bands which would break the
    demo's talk track fails in the domain tests first.
    """
    assert compute_status(patient_due_in(-24), TODAY) is PatientStatus.OVERDUE


# ---------------------------------------------------------------------------------------------
# SCHEDULED — and its expiry
# ---------------------------------------------------------------------------------------------


def test_scheduled_beats_the_date_derived_band() -> None:
    """An overdue patient with an appointment booked reads as SCHEDULED, not OVERDUE."""
    patient = patient_due_in(-24, scheduled_for=TODAY + timedelta(days=9))

    assert compute_status(patient, TODAY) is PatientStatus.SCHEDULED


def test_an_appointment_today_still_counts_as_scheduled() -> None:
    """The lower edge: the appointment is today and has not happened yet."""
    patient = patient_due_in(-5, scheduled_for=TODAY)

    assert compute_status(patient, TODAY) is PatientStatus.SCHEDULED


def test_scheduled_expires_once_the_date_passes() -> None:
    """SPEC §5.2: "If scheduled_for passes without a completion, the patient falls back."

    This is the rule that keeps the recovery funnel honest. Without expiry, a demo left running
    would show patients permanently "Scheduled" — the appointment was never kept, but the
    dashboard would claim it was pending forever.
    """
    patient = patient_due_in(-24, scheduled_for=TODAY - timedelta(days=1))

    assert compute_status(patient, TODAY) is PatientStatus.OVERDUE


def test_an_expired_appointment_falls_back_to_the_correct_band() -> None:
    """Not just "not SCHEDULED" — it must land in the band its dates imply."""
    yesterday = TODAY - timedelta(days=1)

    assert compute_status(patient_due_in(20, scheduled_for=yesterday), TODAY) is (
        PatientStatus.DUE_SOON
    )
    assert compute_status(patient_due_in(0, scheduled_for=yesterday), TODAY) is PatientStatus.DUE
    assert compute_status(patient_due_in(100, scheduled_for=yesterday), TODAY) is (
        PatientStatus.ACTIVE
    )


# ---------------------------------------------------------------------------------------------
# COMPLETED — and its decay
# ---------------------------------------------------------------------------------------------


def test_a_recent_visit_shows_as_completed() -> None:
    """SPEC §7.3's Maria Castillo: completed 6 days ago, next due ~359 days out."""
    patient = patient_due_in(359, last_visit_days_ago=6)

    assert compute_status(patient, TODAY) is PatientStatus.COMPLETED


def test_completed_on_the_day_of_the_visit() -> None:
    """The upper edge: staff mark a visit complete the moment it happens."""
    patient = patient_due_in(365, last_visit_days_ago=0)

    assert compute_status(patient, TODAY) is PatientStatus.COMPLETED


def test_completed_at_its_lower_edge() -> None:
    """Exactly 30 days after the visit is the last day of the confirmation window."""
    patient = patient_due_in(335, last_visit_days_ago=COMPLETED_DISPLAY_WINDOW_DAYS)

    assert compute_status(patient, TODAY) is PatientStatus.COMPLETED


def test_completed_decays_to_active_after_thirty_days() -> None:
    """SPEC §5.2: COMPLETED is "a transient display state ... then it decays to ACTIVE".

    The patient has not changed — only the calendar has. The confirmation has served its purpose
    and the patient rejoins the normal population.
    """
    patient = patient_due_in(334, last_visit_days_ago=COMPLETED_DISPLAY_WINDOW_DAYS + 1)

    assert compute_status(patient, TODAY) is PatientStatus.ACTIVE


def test_a_future_last_visit_date_does_not_trigger_completed() -> None:
    """Guards the lower bound of the window.

    A future visit date should never reach the database — the CSV importer rejects it (SPEC §7.1)
    — but ``compute_status`` is total and must give a sensible answer for any input rather than
    quietly reporting a visit that has not happened.
    """
    patient = patient_due_in(365, last_visit_days_ago=-5)

    assert compute_status(patient, TODAY) is PatientStatus.ACTIVE


# ---------------------------------------------------------------------------------------------
# INACTIVE — precedence over everything (SPEC §5.4: "opted-out precedence")
# ---------------------------------------------------------------------------------------------


def test_paused_reminders_yield_inactive() -> None:
    assert compute_status(patient_due_in(-24, reminders_enabled=False), TODAY) is (
        PatientStatus.INACTIVE
    )


def test_an_opted_out_patient_is_inactive() -> None:
    opted_out = datetime(2026, 5, 1, tzinfo=UTC)

    assert compute_status(patient_due_in(-24, opted_out_at=opted_out), TODAY) is (
        PatientStatus.INACTIVE
    )


@pytest.mark.parametrize(
    ("days_until_due", "description"),
    [(-400, "long overdue"), (-1, "just due"), (0, "due today"), (15, "due soon"), (200, "active")],
)
def test_inactive_beats_every_date_derived_band(days_until_due: int, description: str) -> None:
    """Precedence is top-to-bottom (SPEC §5.2) and INACTIVE is the first row.

    A patient who has withdrawn consent must not appear in an OVERDUE list, because that list is
    a work queue — surfacing them there invites staff to chase someone who asked to be left
    alone, which is the exact mistake the opt-out exists to prevent.
    """
    opted_out = datetime(2026, 5, 1, tzinfo=UTC)
    patient = patient_due_in(days_until_due, opted_out_at=opted_out)

    assert compute_status(patient, TODAY) is PatientStatus.INACTIVE, description


def test_inactive_beats_scheduled_and_completed() -> None:
    """Even the two bands that outrank the date logic lose to INACTIVE."""
    opted_out = datetime(2026, 5, 1, tzinfo=UTC)

    scheduled = patient_due_in(-24, scheduled_for=TODAY + timedelta(days=9), opted_out_at=opted_out)
    assert compute_status(scheduled, TODAY) is PatientStatus.INACTIVE

    completed = patient_due_in(359, last_visit_days_ago=6, reminders_enabled=False)
    assert compute_status(completed, TODAY) is PatientStatus.INACTIVE


def test_pausing_and_opting_out_are_independent() -> None:
    """Either one alone is enough to stop reminders; they are not the same thing.

    Staff pausing is reversible from the UI. A patient opting out is not — only they can undo it,
    through the link in the email they were sent.
    """
    assert compute_status(patient_due_in(10, reminders_enabled=False), TODAY) is (
        PatientStatus.INACTIVE
    )
    assert (
        compute_status(patient_due_in(10, opted_out_at=datetime(2026, 5, 1, tzinfo=UTC)), TODAY)
        is PatientStatus.INACTIVE
    )
    # Neither set: the ordinary path.
    assert compute_status(patient_due_in(10), TODAY) is PatientStatus.DUE_SOON


# ---------------------------------------------------------------------------------------------
# Totality
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("days", range(-40, 41))
def test_every_day_in_a_wide_window_yields_a_valid_status(days: int) -> None:
    """The bands must tile the integers with no gap and no overlap.

    SPEC §5.2's table has four date-derived rows; if their comparisons did not meet exactly,
    some day would fall through every branch. Walking 81 consecutive days proves they do.
    """
    result = compute_status(patient_due_in(days), TODAY)

    assert isinstance(result, PatientStatus)


def test_the_bands_change_exactly_three_times_across_the_window() -> None:
    """There are precisely three boundaries between ACTIVE, DUE_SOON, DUE and OVERDUE.

    A fourth transition would mean a band had been split; a second would mean one had been
    swallowed. Counting them catches both without hardcoding where they fall.
    """
    statuses = [compute_status(patient_due_in(days), TODAY) for days in range(-40, 41)]
    # pairwise() walks consecutive pairs, which says what is meant more directly than
    # zipping a list against its own tail.
    transitions = sum(1 for a, b in pairwise(statuses) if a != b)

    assert transitions == 3
    assert statuses[0] is PatientStatus.OVERDUE
    assert statuses[-1] is PatientStatus.ACTIVE
