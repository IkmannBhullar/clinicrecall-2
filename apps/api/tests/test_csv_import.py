"""CSV import (SPEC §7).

The centrepiece is ``test_the_messy_sample_produces_the_demo_numbers``. SPEC §7.2 requires it in
those words:

    "Add a pytest that runs the validator over this exact file and asserts those four numbers.
     If the file drifts, the test fails before your demo does."

Those numbers are spoken aloud in front of a clinic owner, so they are not approximate. Everything
else here tests the validation rules that produce them.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.clinic_settings import ClinicSettings
from app.models.enums import ActivityEventType, PatientStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.services.csv_import import (
    CsvImportError,
    CsvImportService,
    DateProblemError,
    parse_visit_date,
    validate_visit_date,
)

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "samples"
MESSY_SAMPLE = SAMPLES_DIR / "patients-messy.csv"
CLEAN_SAMPLE = SAMPLES_DIR / "patients-sample.csv"


def csv_stream(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def build_csv(*rows: str, header: str | None = None) -> io.BytesIO:
    """A CSV with the standard header and the given data rows."""
    default_header = "first_name,last_name,email,phone,last_annual_visit_date,external_id"
    return csv_stream("\n".join([header or default_header, *rows]) + "\n")


@pytest.fixture
def importer(db: Session) -> CsvImportService:
    return CsvImportService(db)


def days_ago(count: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=count)).isoformat()


# ---------------------------------------------------------------------------------------------
# THE DEMO NUMBERS (SPEC §7.2)
# ---------------------------------------------------------------------------------------------


def test_the_messy_sample_produces_the_demo_numbers(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.2, asserted against the shipped file.

        327 records found
        320 ready to import
          5 missing required information
          2 invalid email addresses

    This is the test that fails before the demo does. If someone edits the sample, or changes a
    validation rule, the four numbers on the preview screen stop matching the talk track — and
    the person noticing would otherwise be the clinic owner.
    """
    assert MESSY_SAMPLE.exists(), (
        f"{MESSY_SAMPLE} is missing. Generate it with: python3 scripts/generate-sample-csvs.py"
    )

    with MESSY_SAMPLE.open("rb") as handle:
        preview = importer.build_preview(organization.id, handle)

    assert preview.total_rows == 327, "records found"
    assert preview.valid_rows == 320, "ready to import"
    assert preview.missing_required == 5, "missing required information"
    assert preview.invalid_email == 2, "invalid email addresses"


def test_no_row_in_the_messy_sample_is_unaccounted_for(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "Never silently drop a row."

    Every row lands in exactly one bucket, so the buckets must sum to the total. This is also the
    arithmetic behind the preview screen — if it did not hold, the numbers would not add up in
    front of the person reading them.
    """
    with MESSY_SAMPLE.open("rb") as handle:
        preview = importer.build_preview(organization.id, handle)

    accounted = (
        preview.valid_rows
        + preview.missing_required
        + preview.invalid_email
        + preview.invalid_date
        + preview.duplicate_in_file
    )

    assert accounted == preview.total_rows
    assert len(preview.problems) == preview.total_rows - preview.valid_rows


def test_every_problem_names_a_row_a_value_and_a_reason(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1's error report needs all three to be actionable."""
    with MESSY_SAMPLE.open("rb") as handle:
        preview = importer.build_preview(organization.id, handle)

    for problem in preview.problems:
        assert problem.row_number >= 2, "row 1 is the header"
        assert problem.column
        assert problem.reason
        # Written for a receptionist, not a parser.
        assert "Error" not in problem.reason
        assert "Traceback" not in problem.reason


def test_the_clean_sample_has_no_problems(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """The happy-path file must actually be a happy path."""
    with CLEAN_SAMPLE.open("rb") as handle:
        preview = importer.build_preview(organization.id, handle)

    assert preview.total_rows == 40
    assert preview.valid_rows == 40
    assert preview.problems == []


def test_the_samples_use_several_date_formats(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1 requires accepting several formats, so the sample should exercise them.

    A sample containing only ISO dates would let a format regression ship unnoticed.
    """
    text = CLEAN_SAMPLE.read_text()

    assert "/" in text, "no slash-formatted dates in the sample"
    assert any(month in text for month in ("Jan", "Feb", "Mar", "Apr", "May", "Jun")), (
        "no textual month dates in the sample"
    )


def test_the_samples_contain_only_synthetic_contact_details() -> None:
    """SPEC §7.3: emails on @example.com, phone numbers in the 555 range.

    These files are committed to a repository and imported during demos. Nothing in them may
    resolve to a real person.
    """
    for path in (MESSY_SAMPLE, CLEAN_SAMPLE):
        for line in path.read_text().splitlines()[1:]:
            if not line.strip():
                continue
            # Skip the deliberately broken rows.
            if "@" in line:
                for field in line.split(","):
                    if "@" in field and field.strip():
                        assert "example.com" in field or field.strip('"') in {
                            "patricia.wilson@",
                            "not an email address",
                        }, f"non-synthetic email in {path.name}: {field}"


# ---------------------------------------------------------------------------------------------
# Date parsing (SPEC §7.1)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-01-15", (2024, 1, 15)),
        ("2024/01/15", (2024, 1, 15)),
        ("01/15/2024", (2024, 1, 15)),
        ("1/5/2024", (2024, 1, 5)),
        ("01-15-2024", (2024, 1, 15)),
        ("Jan 15, 2024", (2024, 1, 15)),
        ("January 15, 2024", (2024, 1, 15)),
        ("15 Jan 2024", (2024, 1, 15)),
        ("15 January 2024", (2024, 1, 15)),
        ("  2024-01-15  ", (2024, 1, 15)),
    ],
)
def test_accepted_date_formats(raw: str, expected: tuple[int, int, int]) -> None:
    """Every format a practice management system is likely to export."""
    parsed = parse_visit_date(raw)

    assert (parsed.year, parsed.month, parsed.day) == expected


def test_a_day_first_date_is_refused_rather_than_guessed() -> None:
    """SPEC §7.1: "reject ambiguous ones with a clear message".

    ``13/04/2025`` is unambiguous in isolation — there is no month 13 — but accepting it would
    mean one file silently using two different conventions, and the rows that *are* ambiguous
    (``03/04/2025``) would still be read the other way. Refusing keeps the file consistent.
    """
    with pytest.raises(DateProblemError, match="day-first"):
        parse_visit_date("13/04/2025")


def test_the_refusal_says_what_to_do_about_it() -> None:
    """An error a receptionist cannot act on is not much better than no error."""
    with pytest.raises(DateProblemError) as caught:
        parse_visit_date("25/12/2024")

    message = str(caught.value)
    assert "2024-01-15" in message, "the message should show the format to use"


def test_a_two_digit_year_is_refused() -> None:
    """ "01/15/24" could be 1924 or 2024, and nothing in the file distinguishes them."""
    with pytest.raises(DateProblemError, match="two-digit year"):
        parse_visit_date("01/15/24")


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "2024", "15th of January", "??"])
def test_unrecognisable_dates_are_refused_cleanly(raw: str) -> None:
    with pytest.raises(DateProblemError):
        parse_visit_date(raw)


def test_a_date_that_does_not_exist_is_refused() -> None:
    """Correctly shaped, but there is no 30th of February."""
    with pytest.raises(DateProblemError, match="not a real date"):
        parse_visit_date("02/30/2024")


def test_a_future_visit_date_is_refused() -> None:
    """SPEC §7.1: "Reject future last_annual_visit_date."

    A future visit date pushes the next due date beyond a full cycle and quietly removes the
    patient from every recall list — the exact opposite of what importing them was for.
    """
    today = datetime.now(UTC).date()

    with pytest.raises(DateProblemError, match="in the future"):
        validate_visit_date(today + timedelta(days=1), today)


def test_todays_date_is_accepted() -> None:
    """The boundary: a patient seen this morning is a normal import, not an error."""
    today = datetime.now(UTC).date()

    validate_visit_date(today, today)  # must not raise


# ---------------------------------------------------------------------------------------------
# Row validation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected_column"),
    [
        (f",Smith,a@example.com,,{days_ago(100)},", "first_name"),
        (f"Ann,,a@example.com,,{days_ago(100)},", "last_name"),
        (f"Ann,Smith,,,{days_ago(100)},", "email"),
        ("Ann,Smith,a@example.com,,,", "last_annual_visit_date"),
    ],
)
def test_a_missing_required_field_is_reported_against_its_column(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
    row: str,
    expected_column: str,
) -> None:
    preview = importer.build_preview(organization.id, build_csv(row))

    assert preview.missing_required == 1
    assert preview.problems[0].column == expected_column


@pytest.mark.parametrize(
    "email", ["notanemail", "missing@domain", "@nolocal.com", "two@@example.com", "spaces in@x.com"]
)
def test_invalid_emails_are_reported(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
    email: str,
) -> None:
    preview = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,{email},,{days_ago(100)},")
    )

    assert preview.invalid_email == 1
    assert preview.valid_rows == 0


def test_email_case_is_normalised(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "normalize case".

    Matches the functional unique index on ``lower(email)``, so dedupe and storage agree.
    """
    preview = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,Ann.Smith@Example.COM,,{days_ago(100)},")
    )

    assert preview.rows[0].email == "ann.smith@example.com"


def test_a_row_reports_only_its_first_problem(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """A row with three things wrong is still one row to fix.

    Listing every fault separately makes the error report longer without making it more useful.
    """
    preview = importer.build_preview(organization.id, build_csv(",,,,,"))

    assert len(preview.problems) <= 1


def test_blank_lines_are_ignored_rather_than_reported(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """A trailing newline is not a data problem, and flagging it would put a meaningless entry at
    the bottom of every error report."""
    preview = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,a@example.com,,{days_ago(100)},", "", "")
    )

    assert preview.total_rows == 1
    assert preview.problems == []


def test_a_row_of_only_commas_is_reported_not_dropped(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "Never silently drop a row."

    ",,,,," is not a blank line — it is a row with the right number of columns and nothing in
    them. An earlier version of the parser skipped it along with trailing newlines, which meant
    the practice would have believed that patient was imported.
    """
    preview = importer.build_preview(organization.id, build_csv(",,,,,"))

    assert preview.total_rows == 1
    assert preview.missing_required == 1
    assert preview.valid_rows == 0


def test_a_duplicate_within_the_file_is_reported(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """The same patient listed twice in one export — common, and worth saying so."""
    row = f"Ann,Smith,ann@example.com,,{days_ago(100)},"
    preview = importer.build_preview(organization.id, build_csv(row, row))

    assert preview.total_rows == 2
    assert preview.valid_rows == 1
    assert preview.duplicate_in_file == 1
    assert "row 2" in preview.problems[0].reason


# ---------------------------------------------------------------------------------------------
# File-level problems
# ---------------------------------------------------------------------------------------------


def test_a_file_missing_required_columns_is_refused_with_a_useful_message(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """Names the missing columns *and* lists what the file needs — someone has to fix this."""
    with pytest.raises(CsvImportError) as caught:
        importer.build_preview(
            organization.id, build_csv("Ann,Smith", header="first_name,last_name")
        )

    message = str(caught.value)
    assert "email" in message
    assert "last_annual_visit_date" in message


def test_an_empty_file_is_refused(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    with pytest.raises(CsvImportError, match="empty"):
        importer.build_preview(organization.id, csv_stream(""))


def test_an_excel_byte_order_mark_does_not_break_the_first_column(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """Excel writes a BOM on every CSV it exports.

    Without ``utf-8-sig`` the first column is named "﻿first_name", so every single row
    appears to be missing a first name — a baffling failure, and one that would hit the very
    first file a real clinic tries.
    """
    content = (
        "﻿first_name,last_name,email,phone,last_annual_visit_date,external_id\n"
        f"Ann,Smith,ann@example.com,,{days_ago(100)},\n"
    )
    preview = importer.build_preview(organization.id, csv_stream(content))

    assert preview.valid_rows == 1
    assert preview.problems == []


def test_column_headers_are_matched_case_insensitively(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """Exports capitalise headers inconsistently, and that is not the practice's fault."""
    preview = importer.build_preview(
        organization.id,
        build_csv(
            f"Ann,Smith,ann@example.com,,{days_ago(100)},",
            header="First_Name,Last_Name,EMAIL,Phone,Last_Annual_Visit_Date,External_ID",
        ),
    )

    assert preview.valid_rows == 1


# ---------------------------------------------------------------------------------------------
# New vs update (SPEC §7.1)
# ---------------------------------------------------------------------------------------------


def test_the_preview_separates_new_patients_from_updates(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "the preview must say 'X new, Y updates' distinctly".

    Importing an export twice is normal, and someone about to do it needs to know whether they
    are adding 300 patients or updating the 300 they already have.
    """
    first = importer.build_preview(
        organization.id,
        build_csv(
            f"Ann,Smith,ann@example.com,,{days_ago(200)},MRN-1",
            f"Bob,Jones,bob@example.com,,{days_ago(200)},MRN-2",
        ),
    )
    importer.commit(organization.id, first)
    db.flush()

    second = importer.build_preview(
        organization.id,
        build_csv(
            f"Ann,Smith,ann@example.com,,{days_ago(100)},MRN-1",
            f"Cara,Lee,cara@example.com,,{days_ago(100)},MRN-3",
        ),
    )

    assert second.new_count == 1
    assert second.update_count == 1


def test_dedupe_prefers_external_id_over_email(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "Dedupe by external_id first, then email."

    The practice's own identifier is the stronger signal — it survives a marriage, a name change,
    or a new email address, all of which are exactly when an email match would fail.
    """
    preview = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,ann.old@example.com,,{days_ago(200)},MRN-1")
    )
    importer.commit(organization.id, preview)
    db.flush()

    # Same patient, new email address.
    updated = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,ann.new@example.com,,{days_ago(100)},MRN-1")
    )

    assert updated.update_count == 1
    assert updated.new_count == 0


def test_an_import_never_rewinds_a_more_recent_visit(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """Re-importing an older export must not undo a visit recorded in the app since.

    Rewinding would make the patient overdue again and send a reminder to someone who was seen
    last week — which is precisely the mistake that makes staff stop trusting the tool.
    """
    recent = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,ann@example.com,,{days_ago(10)},MRN-1")
    )
    importer.commit(organization.id, recent)
    db.flush()

    stale = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,ann@example.com,,{days_ago(400)},MRN-1")
    )
    importer.commit(organization.id, stale)
    db.flush()

    patient = PatientRepository(db).get_by_email(organization.id, "ann@example.com")
    assert patient is not None
    assert patient.last_annual_visit_date == datetime.now(UTC).date() - timedelta(days=10)


# ---------------------------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------------------------


def test_committing_creates_patients_with_derived_fields(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """Imported patients must arrive with a correct status and due date.

    This is the path that would have been broken by the unflushed-defaults bug found in phase 3:
    every imported patient would have been marked INACTIVE and nobody would have been reminded.
    """
    preview = importer.build_preview(
        organization.id, build_csv(f"Ann,Smith,ann@example.com,,{days_ago(400)},MRN-1")
    )
    importer.commit(organization.id, preview)
    db.flush()

    patient = PatientRepository(db).get_by_email(organization.id, "ann@example.com")

    assert patient is not None
    assert patient.reminders_enabled is True
    assert patient.status is PatientStatus.OVERDUE
    assert patient.next_annual_due_date == patient.last_annual_visit_date.replace(
        year=patient.last_annual_visit_date.year + 1
    )


def test_the_import_writes_one_summary_activity_event(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    staff_user: User,
    importer: CsvImportService,
) -> None:
    """One event, not one per patient.

    Importing 5,000 patients must not bury the activity feed under 5,000 identical lines.
    """
    preview = importer.build_preview(
        organization.id,
        build_csv(
            f"Ann,Smith,ann@example.com,,{days_ago(100)},",
            f"Bob,Jones,bob@example.com,,{days_ago(100)},",
        ),
    )
    importer.commit(organization.id, preview, actor_user_id=staff_user.id)
    db.flush()

    events = ActivityEventRepository(db).list_by_type(
        organization.id, ActivityEventType.PATIENT_IMPORTED
    )

    assert len(events) == 1
    assert events[0].payload["created"] == 2
    assert events[0].actor_user_id == staff_user.id
    # Counts only — no names or addresses (SPEC §9).
    assert "ann@example.com" not in str(events[0].payload)


def test_the_import_is_scoped_to_one_organization(
    db: Session,
    organization: Organization,
    other_organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """The same patient imported into two practices is two separate records."""
    row = f"Ann,Smith,ann@example.com,,{days_ago(100)},MRN-1"

    importer.commit(organization.id, importer.build_preview(organization.id, build_csv(row)))
    importer.commit(
        other_organization.id, importer.build_preview(other_organization.id, build_csv(row))
    )
    db.flush()

    assert PatientRepository(db).count(organization.id) == 1
    assert PatientRepository(db).count(other_organization.id) == 1


# ---------------------------------------------------------------------------------------------
# The error report (SPEC §7.1)
# ---------------------------------------------------------------------------------------------


def test_the_error_report_is_a_usable_csv(
    db: Session,
    organization: Organization,
    clinic_settings: ClinicSettings,
    importer: CsvImportService,
) -> None:
    """SPEC §7.1: "original row number, the offending value, and a plain-English reason".

    A list of 200 errors on screen is unusable; a file someone can sort and work through is not.
    """
    import csv as csv_module

    with MESSY_SAMPLE.open("rb") as handle:
        preview = importer.build_preview(organization.id, handle)

    report = importer.build_error_report(preview)
    rows = list(csv_module.reader(io.StringIO(report)))

    assert rows[0] == ["row_number", "problem", "column", "value", "what_to_do"]
    assert len(rows) - 1 == len(preview.problems)

    # Sorted by row number, so it lines up with the file the practice has open.
    row_numbers = [int(row[0]) for row in rows[1:]]
    assert row_numbers == sorted(row_numbers)

    for row in rows[1:]:
        assert row[4], "every row needs an explanation"
