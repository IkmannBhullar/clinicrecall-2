"""Date arithmetic for annual recall (SPEC §5.3, §5.4).

These tests need no database and no fixtures, because ``next_annual_due_date`` is a pure
function over two arguments. That is the payoff of keeping the domain core free of I/O.

The leap-year test is the one that justifies the "no hand-rolled date math" rule in SPEC §3. A
naive implementation adds 365 days and is wrong for every patient whose last visit fell in a leap
year; a slightly less naive one increments the year field and raises ``ValueError`` on 29
February. ``relativedelta`` handles it, and the test below pins the behaviour so nobody
"optimises" it away later.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.recall import next_annual_due_date

# ---------------------------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------------------------


def test_twelve_months_lands_on_the_same_day_next_year() -> None:
    assert next_annual_due_date(date(2025, 3, 14), 12) == date(2026, 3, 14)


def test_the_result_is_a_calendar_date_not_a_timestamp() -> None:
    """SPEC §5.3: "All due dates are calendar dates, never timestamps."

    "Due on the 14th" is a fact about a clinic's day, not an instant in time. Storing it as a
    timestamp immediately raises the question of whose midnight it is.
    """
    result = next_annual_due_date(date(2025, 6, 1), 12)

    assert isinstance(result, date)
    assert not hasattr(result, "hour")


@pytest.mark.parametrize(
    ("last_visit", "expected"),
    [
        (date(2025, 1, 31), date(2026, 1, 31)),
        (date(2025, 12, 31), date(2026, 12, 31)),
        (date(2025, 6, 15), date(2026, 6, 15)),
        (date(2024, 7, 4), date(2025, 7, 4)),
    ],
)
def test_month_ends_and_ordinary_dates_round_trip(last_visit: date, expected: date) -> None:
    assert next_annual_due_date(last_visit, 12) == expected


# ---------------------------------------------------------------------------------------------
# Leap years (SPEC §5.3 names this explicitly)
# ---------------------------------------------------------------------------------------------


def test_february_29_plus_twelve_months_becomes_february_28() -> None:
    """SPEC §5.3: "Feb 29 + 12 months → Feb 28 (relativedelta's behavior; assert it in a test)."

    2024 is a leap year, 2025 is not. There is no 29 February 2025, so the only sensible answer
    is the 28th. Asserted here so the behaviour is a decision rather than an accident.
    """
    assert next_annual_due_date(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_february_29_plus_forty_eight_months_returns_to_february_29() -> None:
    """Four years later is a leap year again, so the original day survives the round trip."""
    assert next_annual_due_date(date(2024, 2, 29), 48) == date(2028, 2, 29)


def test_february_28_in_a_leap_year_stays_february_28() -> None:
    """The clamping only applies to the 29th — the 28th is unremarkable."""
    assert next_annual_due_date(date(2024, 2, 28), 12) == date(2025, 2, 28)


def test_a_leap_day_crossing_does_not_shift_ordinary_dates() -> None:
    """A year spanning 29 February is still a year, not 366 days of drift.

    This is what a "+365 days" implementation gets wrong: it would return 1 March 2025 here.
    """
    assert next_annual_due_date(date(2024, 3, 1), 12) == date(2025, 3, 1)
    assert next_annual_due_date(date(2023, 3, 1), 12) == date(2024, 3, 1)


# ---------------------------------------------------------------------------------------------
# Interval override from settings (SPEC §5.3, §5.4)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("interval_months", "expected"),
    [
        (6, date(2025, 9, 14)),
        (12, date(2026, 3, 14)),
        (18, date(2026, 9, 14)),
        (24, date(2027, 3, 14)),
    ],
)
def test_the_interval_comes_from_settings_not_a_constant(
    interval_months: int, expected: date
) -> None:
    """SPEC §5.3: the interval comes from clinic_settings, "not a constant".

    A practice running an 18-month recall cycle is a legitimate customer, and a hardcoded 12
    would give them a wrong due date for every single patient — silently, and in a way that looks
    entirely plausible.
    """
    assert next_annual_due_date(date(2025, 3, 14), interval_months) == expected


def test_a_non_twelve_interval_still_clamps_a_leap_day() -> None:
    """The leap-day rule is a property of the calendar, not of the 12-month case."""
    assert next_annual_due_date(date(2024, 2, 29), 36) == date(2027, 2, 28)


# ---------------------------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("interval_months", [0, -1, -12])
def test_a_non_positive_interval_is_rejected(interval_months: int) -> None:
    """There is no sensible answer for a zero or negative recall cycle.

    Rather than return a date in the past — which would mark every patient in the practice
    permanently overdue and look like a data problem rather than a configuration one — this
    raises. The database enforces the same rule with a CHECK constraint, so both layers agree.
    """
    with pytest.raises(ValueError, match="must be positive"):
        next_annual_due_date(date(2025, 3, 14), interval_months)
