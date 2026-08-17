#!/usr/bin/env python3
"""Generate the shipped sample CSVs (SPEC §7.2).

Two files, with different jobs:

``docs/samples/patients-sample.csv``
    ~40 clean rows. What a practice's export looks like when nothing is wrong. Used to show the
    happy path, and as the file someone tries first.

``docs/samples/patients-messy.csv``
    Constructed to produce **exactly** the numbers in the demo script::

        327 records found
        320 ready to import
          5 missing required information
          2 invalid email addresses

    Those numbers are spoken aloud during the demo, so they are not approximate. A pytest asserts
    all four against this file, which is what makes a drift fail before the demo does rather than
    during it.

WHY THIS IS GENERATED RATHER THAN HAND-WRITTEN
Every date is computed as ``today - N days`` (SPEC constraint D1). A file with hardcoded dates is
subtly wrong within a week: imported patients land in the wrong recall bands, and the status
badges stop matching what the talk track claims.

The generated dates are all in the past and stay in the past, so the four headline counts are
stable no matter how long the committed file sits there — it is the *realism* that decays, not
the validity. `make setup` regenerates both files so a demo always imports fresh dates.

Run directly, or via `make samples`.
"""

from __future__ import annotations

import csv
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "docs" / "samples"

# The demo numbers from SPEC §7.2. Named constants rather than literals scattered below, so the
# arithmetic is checkable at a glance and the test can import the same values.
TOTAL_ROWS = 327
VALID_ROWS = 320
MISSING_REQUIRED_ROWS = 5
INVALID_EMAIL_ROWS = 2

assert VALID_ROWS + MISSING_REQUIRED_ROWS + INVALID_EMAIL_ROWS == TOTAL_ROWS, (
    "The demo numbers must add up, or the preview screen will not show them."
)

# A fixed seed. The file has to be byte-identical every time it is generated, apart from the
# dates moving — otherwise regenerating it before a demo would quietly reshuffle the rows and
# any screenshot or talk-track reference to "the third row" stops being true.
RANDOM_SEED = 20260817

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David",
    "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas",
    "Sarah", "Charles", "Karen", "Christopher", "Nancy", "Daniel", "Lisa", "Matthew", "Betty",
    "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley", "Steven", "Kimberly", "Paul",
    "Emily", "Andrew", "Donna", "Joshua", "Michelle", "Kenneth", "Carol", "Kevin", "Amanda",
    "Brian", "Dorothy", "George", "Melissa", "Timothy", "Deborah", "Priya", "Wei", "Amara",
    "Diego", "Fatima", "Yusuf", "Ingrid", "Tomas", "Aisha", "Mateo", "Hana", "Omar", "Sofia",
    "Kwame", "Elena", "Rahul", "Nadia", "Luca", "Chiara", "Ravi", "Mei",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez",
    "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker",
    "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Okafor", "Castillo",
    "Brennan", "Tran", "Hale", "Chen", "Patel", "Kowalski", "Andersen", "Dubois", "Rossi",
    "Nakamura", "Osei", "Haddad", "Silva", "Novak", "Ivanov", "Bergstrom", "Kaur", "Mensah",
]

# The date formats a real export mixes. All unambiguous — parsing accepts every one of these,
# which is the point of shipping them in the sample (SPEC §7.1: "Accept several date formats").
DATE_STYLES = ("iso", "us_slash", "text_short", "text_long")


def format_date(value: datetime, style: str) -> str:
    match style:
        case "iso":
            return value.strftime("%Y-%m-%d")
        case "us_slash":
            return value.strftime("%m/%d/%Y")
        case "text_short":
            return value.strftime("%b %d, %Y")
        case "text_long":
            return value.strftime("%d %B %Y")
    raise ValueError(f"unknown date style {style!r}")


def build_person(rng: random.Random, index: int, today: datetime) -> dict[str, str]:
    """One clean row, with a date somewhere in the last two years.

    All identities fictional, emails on @example.com, phone numbers in the 555 range (SPEC §7.3).
    """
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)

    # Spread across roughly two years so an import produces a realistic mix of recall bands
    # rather than everyone landing in the same status.
    days_ago = rng.randint(30, 730)
    visit_date = today - timedelta(days=days_ago)

    return {
        "first_name": first,
        "last_name": last,
        # The index keeps addresses unique — two "James Smith"s in one file would otherwise
        # collide on the email dedupe rule and be reported as duplicates.
        "email": f"{first.lower()}.{last.lower()}{index}@example.com",
        "phone": f"555-{rng.randint(100, 999):03d}-{rng.randint(1000, 9999):04d}",
        "last_annual_visit_date": format_date(visit_date, rng.choice(DATE_STYLES)),
        "external_id": f"MRN-{100000 + index}",
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["first_name", "last_name", "email", "phone", "last_annual_visit_date", "external_id"]

    with path.open("w", newline="", encoding="utf-8") as handle:
        # lineterminator="\n" rather than csv's default "\r\n".
        #
        # RFC 4180 specifies CRLF, and every CSV reader accepts either — but these files are
        # committed to git, which normalises line endings on checkout. Writing CRLF means the
        # working copy and the committed blob disagree, so `git status` reports the samples as
        # modified immediately after regenerating them, every time.
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate_clean_file(today: datetime) -> Path:
    """~40 rows, nothing wrong with any of them."""
    rng = random.Random(RANDOM_SEED)
    rows = [build_person(rng, index, today) for index in range(1, 41)]

    path = SAMPLES_DIR / "patients-sample.csv"
    write_csv(path, rows)
    print(f"  {path.relative_to(REPO_ROOT)}: {len(rows)} clean rows")
    return path


def generate_messy_file(today: datetime) -> Path:
    """327 rows producing exactly 320 / 5 / 2.

    The broken rows are spread through the file rather than grouped at the end, because a real
    export's problems are scattered — and because a preview that only shows errors from the last
    page is not much of a preview.
    """
    rng = random.Random(RANDOM_SEED + 1)
    rows: list[dict[str, str]] = [
        build_person(rng, index, today) for index in range(1, TOTAL_ROWS + 1)
    ]

    # Deterministic positions, chosen once and then fixed. Regenerating the file must not move
    # the broken rows around, or a screenshot of the preview stops matching.
    missing_positions = [17, 63, 148, 229, 301]
    invalid_email_positions = [45, 192]

    assert len(missing_positions) == MISSING_REQUIRED_ROWS
    assert len(invalid_email_positions) == INVALID_EMAIL_ROWS
    assert not set(missing_positions) & set(invalid_email_positions), (
        "A row cannot be in two buckets — the counts would not add up to the total."
    )

    # Five rows missing required information, each missing a *different* field, so the preview
    # exercises every branch of the required-field check rather than the same one five times.
    missing_fields = ["email", "last_annual_visit_date", "first_name", "last_name", "email"]
    for position, missing_field in zip(missing_positions, missing_fields, strict=True):
        rows[position][missing_field] = ""

    # Two malformed addresses, of the two kinds that actually occur in exported data: a missing
    # domain, and a stray space where a name was pasted in.
    rows[invalid_email_positions[0]]["email"] = "patricia.wilson@"
    rows[invalid_email_positions[1]]["email"] = "not an email address"

    path = SAMPLES_DIR / "patients-messy.csv"
    write_csv(path, rows)
    print(
        f"  {path.relative_to(REPO_ROOT)}: {TOTAL_ROWS} rows "
        f"({VALID_ROWS} valid, {MISSING_REQUIRED_ROWS} missing required, "
        f"{INVALID_EMAIL_ROWS} invalid email)"
    )
    return path


def main() -> None:
    # Dates are relative to today (SPEC constraint D1), so regenerating before a demo keeps the
    # imported patients landing in believable recall bands.
    today = datetime.now(UTC)

    print(f"Generating sample CSVs relative to {today.date().isoformat()}")
    generate_clean_file(today)
    generate_messy_file(today)
    print("Done.")


if __name__ == "__main__":
    main()
