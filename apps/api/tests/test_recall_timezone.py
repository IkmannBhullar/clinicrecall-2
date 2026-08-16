"""Timezone handling (SPEC §5.3, §5.4 "timezone boundary (23:30 local vs UTC)").

The bug this guards against is specific and easy to ship: the server runs in UTC, the practice is
in California, and at 21:00 Pacific it is already tomorrow in UTC. If "today" came from
``date.today()`` on the server, every patient would appear one day further along than they
actually are — a demo at 9pm would display tomorrow's statuses, and a patient due tomorrow would
show as DUE.

``time-machine`` is used to move the clock. It patches at the C level, so
``datetime.now(ZoneInfo(...))`` genuinely returns the frozen instant rather than a mock object,
and the code under test is exercised exactly as it runs in production.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import time_machine

from app.models.enums import PatientStatus
from app.services.recall import (
    PatientRecallInput,
    compute_status,
    today_for_timezone,
)

LOS_ANGELES = "America/Los_Angeles"
NEW_YORK = "America/New_York"
LONDON = "Europe/London"
TOKYO = "Asia/Tokyo"


# ---------------------------------------------------------------------------------------------
# The boundary case SPEC §5.4 names
# ---------------------------------------------------------------------------------------------


@time_machine.travel(datetime(2026, 6, 16, 6, 30, tzinfo=UTC), tick=False)
def test_late_evening_in_california_is_still_today_there() -> None:
    """06:30 UTC on the 16th is 23:30 on the 15th in Los Angeles.

    The practice's date must be the 15th. A naive ``date.today()`` on a UTC server would say the
    16th and roll every status forward a day — which is precisely the "demo at 9pm Pacific"
    failure SPEC §5.3 calls out.
    """
    assert today_for_timezone(LOS_ANGELES) == date(2026, 6, 15)


@time_machine.travel(datetime(2026, 6, 16, 6, 30, tzinfo=UTC), tick=False)
def test_the_same_instant_is_a_different_date_in_different_practices() -> None:
    """One moment, four calendars. This is why "today" is a per-practice question."""
    assert today_for_timezone(LOS_ANGELES) == date(2026, 6, 15)  # 23:30 on the 15th
    assert today_for_timezone(NEW_YORK) == date(2026, 6, 16)  # 02:30 on the 16th
    assert today_for_timezone(LONDON) == date(2026, 6, 16)  # 07:30 on the 16th
    assert today_for_timezone(TOKYO) == date(2026, 6, 16)  # 15:30 on the 16th


@time_machine.travel(datetime(2026, 6, 15, 6, 59, tzinfo=UTC), tick=False)
def test_one_minute_before_midnight_pacific() -> None:
    """23:59 on the 14th in Los Angeles. Still the 14th."""
    assert today_for_timezone(LOS_ANGELES) == date(2026, 6, 14)


@time_machine.travel(datetime(2026, 6, 15, 7, 1, tzinfo=UTC), tick=False)
def test_one_minute_after_midnight_pacific() -> None:
    """00:01 on the 15th in Los Angeles. The date has turned."""
    assert today_for_timezone(LOS_ANGELES) == date(2026, 6, 15)


# ---------------------------------------------------------------------------------------------
# The consequence for status
# ---------------------------------------------------------------------------------------------


@time_machine.travel(datetime(2026, 6, 16, 6, 30, tzinfo=UTC), tick=False)
def test_a_patient_due_tomorrow_is_not_shown_as_due_tonight() -> None:
    """The end-to-end version of the bug.

    It is 23:30 on 15 June in California. A patient due on 16 June is DUE_SOON — one day out.
    Using the UTC date would make ``d`` zero and show them as DUE, a day early, to a room full
    of people.
    """
    practice_today = today_for_timezone(LOS_ANGELES)
    patient = PatientRecallInput(
        next_annual_due_date=date(2026, 6, 16),
        last_annual_visit_date=date(2025, 6, 16),
    )

    assert compute_status(patient, practice_today) is PatientStatus.DUE_SOON

    # And for contrast, what the naive version would have produced.
    utc_today = datetime.now(UTC).date()
    assert utc_today == date(2026, 6, 16)
    assert compute_status(patient, utc_today) is PatientStatus.DUE


# ---------------------------------------------------------------------------------------------
# Daylight saving
# ---------------------------------------------------------------------------------------------


@time_machine.travel(datetime(2026, 3, 8, 10, 30, tzinfo=UTC), tick=False)
def test_daylight_saving_transition_is_handled_by_the_zone_database() -> None:
    """Pacific time is UTC-8 in winter and UTC-7 in summer.

    This is why the settings column stores an IANA name like "America/Los_Angeles" rather than a
    fixed offset: a stored "-08:00" is simply wrong for half the year, and it would be wrong in a
    way that only shows up as a one-day discrepancy on a handful of dates.
    """
    # 10:30 UTC on 8 March 2026, the day US clocks spring forward. Pacific is UTC-7 by then.
    assert today_for_timezone(LOS_ANGELES) == date(2026, 3, 8)


@time_machine.travel(datetime(2026, 1, 15, 3, 0, tzinfo=UTC), tick=False)
def test_winter_offset_rolls_the_date_back() -> None:
    """03:00 UTC in January is 19:00 the previous evening in Los Angeles (UTC-8)."""
    assert today_for_timezone(LOS_ANGELES) == date(2026, 1, 14)


# ---------------------------------------------------------------------------------------------
# Bad configuration
# ---------------------------------------------------------------------------------------------


def test_an_unrecognised_timezone_falls_back_rather_than_raising() -> None:
    """A typo in a settings field must not take down every status in the application.

    Showing dates in the wrong timezone is bad. Showing no dates at all, because one bad string
    raised an exception on every request, is considerably worse — and it would present as a
    total outage rather than as the configuration mistake it is.
    """
    result = today_for_timezone("Not/A_Real_Zone")

    assert isinstance(result, date)


def test_an_empty_timezone_falls_back() -> None:
    assert isinstance(today_for_timezone(""), date)


def test_the_fallback_is_a_plausible_date() -> None:
    """The fallback must return a real current date, not an epoch or a sentinel."""
    fallback = today_for_timezone("Nonsense/Zone")
    utc_today = datetime.now(UTC).date()

    assert abs((fallback - utc_today).days) <= 1
