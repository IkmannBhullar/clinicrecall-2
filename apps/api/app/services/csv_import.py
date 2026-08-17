"""CSV import — parsing, validation, and the commit (SPEC §7).

SPEC §7 opens by calling this "the single most scrutinized screen in the demo", and that is right
for a specific reason: it is the moment a clinic hands over their own data. Everything before it
is a claim about what the product does; this is the first thing that has to actually work on
records they recognise.

Four rules shape the whole module.

**Never silently drop a row.** Every line in the file ends up in exactly one bucket — imported, or
rejected with a row number and a plain-English reason. A row that vanishes without explanation is
worse than one that fails loudly, because the practice believes those patients are covered.

**Reject ambiguity rather than guessing.** ``03/04/2025`` is March 4th to an American clinic and
April 3rd to a British one. Guessing wrong shifts a patient's recall by a month, silently, and
looks entirely plausible on screen.

**Explain in the practice's language, not the parser's.** Every message is written for a
receptionist. "Row 47: the date 2027-01-15 is in the future" — never "ValueError at column 5".

**All or nothing.** The commit runs in one transaction, so a failure halfway through cannot leave
a practice with half their patients imported and no way to know which half.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.enums import ActivityEventType
from app.models.patient import Patient
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.patients import PatientRepository
from app.services.recall import RecallService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# Limits (SPEC §7.1: "Enforce max upload size and max row count")
# ---------------------------------------------------------------------------------------------

#: Largest number of data rows accepted in one file.
#:
#: A very large practice might have 40,000 patients, so this is roomy. The point of the cap is
#: that an unbounded file means unbounded memory and an unbounded transaction — and the request
#: size limit alone does not stop it, since a hundred million rows of "a,b,c" compresses well
#: over the wire.
MAX_ROWS = 50_000

#: Hard byte ceiling, counted while streaming.
#:
#: The middleware already refuses an oversized ``Content-Length``, but a chunked upload sends no
#: length at all — so the parser counts bytes as it reads and stops. Two independent limits,
#: because the first can be bypassed by a client that chooses to.
MAX_BYTES = 10 * 1024 * 1024

#: Columns the file must contain.
REQUIRED_COLUMNS = ("first_name", "last_name", "email", "last_annual_visit_date")

#: Columns that may be present.
OPTIONAL_COLUMNS = ("phone", "external_id")

#: No patient was last seen before this. A date earlier than it is corrupted data, not history.
EARLIEST_PLAUSIBLE_VISIT = date(1900, 1, 1)


class CsvImportError(AppError):
    """The file as a whole cannot be processed — wrong columns, too large, unreadable."""

    code = "IMPORT_FAILED"
    status_code = 422
    message = "That file could not be imported."


# ---------------------------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowProblem:
    """One rejected row, described the way the error-report CSV needs it (SPEC §7.1)."""

    row_number: int
    """Line number in the original file, counting the header as line 1.

    Counting the header matters: the practice will open the file in Excel to fix it, and Excel
    shows the header as row 1. An off-by-one here sends them to the wrong line.
    """

    category: str
    """Which bucket this falls in: missing_required, invalid_email, invalid_date, duplicate."""

    column: str
    value: str
    reason: str
    """Plain English, written for a receptionist rather than an engineer."""


@dataclass
class ParsedRow:
    """A row that passed validation, normalised and ready to import."""

    row_number: int
    first_name: str
    last_name: str
    email: str
    last_annual_visit_date: date
    phone: str | None = None
    external_id: str | None = None


@dataclass
class ImportPreview:
    """What the preview screen shows before anyone commits (SPEC §7.1).

    The four headline numbers are the ones in SPEC §7.2's demo script::

        327 records found
        320 ready to import
          5 missing required information
          2 invalid email addresses
    """

    total_rows: int = 0
    """Every data row in the file. Header excluded."""

    valid_rows: int = 0
    """Rows that will be imported."""

    new_count: int = 0
    """Of those, how many are patients this practice does not already have."""

    update_count: int = 0
    """And how many match an existing patient and will update them (SPEC §7.1)."""

    missing_required: int = 0
    invalid_email: int = 0
    invalid_date: int = 0
    duplicate_in_file: int = 0

    problems: list[RowProblem] = field(default_factory=list)
    rows: list[ParsedRow] = field(default_factory=list)

    @property
    def has_problems(self) -> bool:
        return bool(self.problems)


@dataclass
class ImportResult:
    """What actually happened after the commit."""

    created: int = 0
    updated: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------------------------

# ISO, and the same order with slashes. Unambiguous: a four-digit year first can only be a year.
_ISO_PATTERN = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")

# Numeric, year last. Ambiguous by construction — see `parse_visit_date`.
_NUMERIC_PATTERN = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")

# Two-digit year. Always rejected.
_SHORT_YEAR_PATTERN = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2}$")

# Textual months, either order: "Jan 15, 2024" / "15 January 2024".
_TEXT_FORMATS = ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y")


class DateProblemError(Exception):
    """A date could not be parsed, with a message written for the person who will fix it."""


def parse_visit_date(raw: str) -> date:
    """Parse a last-visit date from any of the formats a practice management system exports.

    :raises DateProblemError: with a message safe to show a receptionist

    **Why day-first dates are refused rather than interpreted.** ``03/04/2025`` is March 4th in the
    United States and April 3rd almost everywhere else. Nothing in the file says which, so any
    choice is a guess — and a wrong guess moves a patient's recall by a month while looking
    entirely correct on screen. ``13/04/2025`` is unambiguous in isolation, but accepting it would
    mean one file silently using two different conventions, which is worse.

    So the rule is: four-digit-year-first is always accepted, month-first numeric is accepted,
    and anything that implies day-first is refused with a message telling the practice exactly
    what to do about it.
    """
    value = raw.strip()

    if not value:
        raise DateProblemError("The last visit date is missing.")

    # 1. ISO — the unambiguous form, and the one the error message recommends.
    match = _ISO_PATTERN.match(value)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return _build_date(year, month, day, value)

    # 2. Two-digit years. "01/15/24" could be 1924 or 2024 and nothing distinguishes them.
    if _SHORT_YEAR_PATTERN.match(value):
        raise DateProblemError(
            f"'{value}' uses a two-digit year, which is ambiguous. "
            "Please write the full year, for example 2024-01-15."
        )

    # 3. Numeric with a four-digit year at the end.
    match = _NUMERIC_PATTERN.match(value)
    if match:
        first, second, year = (int(part) for part in match.groups())

        if first > 12 and second <= 12:
            # Unambiguously day-first — and therefore not the convention the rest of the file
            # is being read with.
            raise DateProblemError(
                f"'{value}' looks like a day-first date (day {first}, month {second}), but dates "
                "in this column are read as month first. Please use the format 2024-01-15."
            )

        if first > 12 and second > 12:
            raise DateProblemError(f"'{value}' is not a valid date.")

        return _build_date(year, first, second, value)

    # 4. Textual months — unambiguous, because the month is spelled out.
    for date_format in _TEXT_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()  # noqa: DTZ007
        except ValueError:
            continue

    raise DateProblemError(
        f"'{value}' is not a date we recognise. Please use the format 2024-01-15."
    )


def _build_date(year: int, month: int, day: int, original: str) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        # Catches 2025-02-30 and similar: correctly shaped, but not a day that exists.
        raise DateProblemError(f"'{original}' is not a real date.") from None


def validate_visit_date(value: date, today: date) -> None:
    """Check a parsed date is plausible as a past visit.

    :raises DateProblemError: if it is not
    """
    if value > today:
        # SPEC §7.1: "Reject future last_annual_visit_date." A future visit date would push the
        # next due date beyond a full cycle and quietly remove the patient from every recall list
        # — the exact opposite of what importing them was for.
        raise DateProblemError(
            f"The last visit date {value.isoformat()} is in the future. "
            "This should be the date the patient was last seen."
        )

    if value < EARLIEST_PLAUSIBLE_VISIT:
        raise DateProblemError(
            f"The last visit date {value.isoformat()} is too far in the past to be correct."
        )


# ---------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------


class CsvImportService:
    """Parses, validates, previews, and commits a patient CSV."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.patients = PatientRepository(session)
        self.activity = ActivityEventRepository(session)
        self.recall = RecallService(session)

    # -----------------------------------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------------------------------

    def build_preview(self, organization_id: uuid.UUID, stream: io.BufferedIOBase) -> ImportPreview:
        """Read the file and work out what importing it would do.

        Nothing is written. This backs the preview screen, which is what SPEC §7.1 requires
        before anyone commits — a clinic handing over their patient list should see what is about
        to happen to it first.
        """
        today = self.recall.today_for_org(organization_id)
        preview = ImportPreview()

        # Seen within *this file*, to catch a file that lists the same patient twice.
        seen_external_ids: dict[str, int] = {}
        seen_emails: dict[str, int] = {}

        for row_number, raw_row in self._read_rows(stream):
            preview.total_rows += 1
            problem = self._validate_row(raw_row, row_number, today, seen_external_ids, seen_emails)

            if problem is not None:
                preview.problems.append(problem)
                self._count_problem(preview, problem)
                continue

            parsed = self._normalise_row(raw_row, row_number)
            preview.rows.append(parsed)
            preview.valid_rows += 1

            if parsed.external_id:
                seen_external_ids[parsed.external_id] = row_number
            seen_emails[parsed.email.lower()] = row_number

            # New or update? Decided by the same dedupe order the commit uses, so the preview
            # cannot promise one thing and the import do another (SPEC §7.1).
            if self._find_existing(organization_id, parsed) is None:
                preview.new_count += 1
            else:
                preview.update_count += 1

        return preview

    def _read_rows(self, stream: io.BufferedIOBase) -> Iterator[tuple[int, dict[str, str]]]:
        """Yield ``(row_number, row)`` pairs, streaming rather than loading the whole file.

        SPEC §7.1: "stream-parse rather than loading whole file". The difference matters at
        50,000 rows — reading it all into a list first means holding the entire file plus a
        parsed copy of it in memory at once, for no benefit.

        ``row_number`` counts the header as line 1, because that is how the practice's
        spreadsheet numbers it and they will be opening the file to fix these rows.
        """
        # A byte counter wrapped around the incoming stream, so a chunked upload with no
        # Content-Length is still bounded.
        counting = _ByteLimitedStream(stream, MAX_BYTES)

        # BufferedReader in the middle for two reasons: TextIOWrapper expects a buffered stream
        # (a raw one is a type error), and it batches reads instead of issuing one call per
        # chunk the decoder asks for.
        buffered = io.BufferedReader(counting)

        # utf-8-sig strips the byte-order mark Excel writes on every CSV it exports. Without it
        # the first column is named "﻿first_name" and every row appears to be missing a
        # first name — which is a genuinely baffling failure to debug.
        text_stream = io.TextIOWrapper(buffered, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(text_stream)

        if reader.fieldnames is None:
            raise CsvImportError("That file appears to be empty.")

        headers = {(name or "").strip().lower() for name in reader.fieldnames}
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise CsvImportError(
                "That file is missing required columns: "
                + ", ".join(missing)
                + ". The file needs a header row with: "
                + ", ".join(REQUIRED_COLUMNS)
                + " (and optionally "
                + ", ".join(OPTIONAL_COLUMNS)
                + ")."
            )

        row_number = 1  # the header
        for raw_row in reader:
            row_number += 1

            if row_number - 1 > MAX_ROWS:
                raise CsvImportError(
                    f"That file has more than {MAX_ROWS:,} rows. "
                    "Please split it into smaller files."
                )

            # NOTE: there is deliberately no "skip rows where every field is empty" check.
            #
            # An earlier version had one, reasoning that a trailing newline should not appear
            # in the error report. But `csv.DictReader` already drops genuinely empty lines
            # before we ever see them, so the only thing that check actually caught was a row
            # like ",,,,," — a real row, with the right number of columns, that happens to be
            # entirely blank. Skipping it is precisely what SPEC 7.1 forbids: the practice
            # would believe that patient had been imported.
            #
            # Such a row now falls through to validation and is reported as missing required
            # information, which is what it is.
            yield (
                row_number,
                {
                    (key or "").strip().lower(): (value or "").strip()
                    for key, value in raw_row.items()
                    if key is not None
                },
            )

    # -----------------------------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------------------------

    def _validate_row(
        self,
        row: dict[str, str],
        row_number: int,
        today: date,
        seen_external_ids: dict[str, int],
        seen_emails: dict[str, int],
    ) -> RowProblem | None:
        """Return the first problem with this row, or None if it is fine.

        First problem only, deliberately. A row with no name and a bad date has one thing wrong
        with it as far as the person fixing the file is concerned — the row — and listing four
        entries for it makes the error report harder to act on, not easier.
        """
        # --- Required fields ---
        for column in ("first_name", "last_name"):
            if not row.get(column):
                return RowProblem(
                    row_number=row_number,
                    category="missing_required",
                    column=column,
                    value="",
                    reason=f"{column.replace('_', ' ').capitalize()} is missing.",
                )

        email = row.get("email", "")
        if not email:
            return RowProblem(
                row_number=row_number,
                category="missing_required",
                column="email",
                value="",
                reason="Email address is missing. Patients cannot be reminded without one.",
            )

        raw_date = row.get("last_annual_visit_date", "")
        if not raw_date:
            return RowProblem(
                row_number=row_number,
                category="missing_required",
                column="last_annual_visit_date",
                value="",
                reason="Last visit date is missing.",
            )

        # --- Email syntax ---
        try:
            # check_deliverability=False: this must never make a DNS query. It would be a runtime
            # network call (SPEC constraint D2), it would make importing a large file take
            # minutes, and a clinic's own domain failing a lookup on conference wifi would reject
            # the entire file.
            validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            return RowProblem(
                row_number=row_number,
                category="invalid_email",
                column="email",
                value=email,
                reason=f"'{email}' is not a valid email address.",
            )

        # --- Date ---
        try:
            parsed_date = parse_visit_date(raw_date)
            validate_visit_date(parsed_date, today)
        except DateProblemError as problem:
            return RowProblem(
                row_number=row_number,
                category="invalid_date",
                column="last_annual_visit_date",
                value=raw_date,
                reason=str(problem),
            )

        # --- Duplicates within the file ---
        external_id = row.get("external_id") or None
        if external_id and external_id in seen_external_ids:
            return RowProblem(
                row_number=row_number,
                category="duplicate",
                column="external_id",
                value=external_id,
                reason=(
                    f"This patient ID also appears on row {seen_external_ids[external_id]}. "
                    "Only the first one will be imported."
                ),
            )

        if email.lower() in seen_emails:
            return RowProblem(
                row_number=row_number,
                category="duplicate",
                column="email",
                value=email,
                reason=(
                    f"This email address also appears on row {seen_emails[email.lower()]}. "
                    "Only the first one will be imported."
                ),
            )

        return None

    @staticmethod
    def _count_problem(preview: ImportPreview, problem: RowProblem) -> None:
        """Tally a problem into its bucket.

        The buckets are disjoint and exhaustive, so ``valid + missing + invalid_email +
        invalid_date + duplicate`` always equals ``total_rows``. A test asserts that, because it
        is the arithmetic behind SPEC §7.2's demo numbers and it is what guarantees no row was
        silently dropped.
        """
        match problem.category:
            case "missing_required":
                preview.missing_required += 1
            case "invalid_email":
                preview.invalid_email += 1
            case "invalid_date":
                preview.invalid_date += 1
            case "duplicate":
                preview.duplicate_in_file += 1

    @staticmethod
    def _normalise_row(row: dict[str, str], row_number: int) -> ParsedRow:
        """Convert a validated row into its stored form.

        Email is lowercased here (SPEC §7.1: "normalize case") so it matches the functional unique
        index on ``lower(email)``. Storing it as typed would still dedupe correctly thanks to the
        index, but every later comparison in application code would need to remember.
        """
        return ParsedRow(
            row_number=row_number,
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"].lower(),
            last_annual_visit_date=parse_visit_date(row["last_annual_visit_date"]),
            phone=row.get("phone") or None,
            external_id=row.get("external_id") or None,
        )

    # -----------------------------------------------------------------------------------------
    # Dedupe
    # -----------------------------------------------------------------------------------------

    def _find_existing(self, organization_id: uuid.UUID, parsed: ParsedRow) -> Patient | None:
        """Find the patient this row refers to, if the practice already has them.

        Order matters and is specified (SPEC §7.1): ``external_id`` first, then email.

        The practice's own identifier is the stronger signal — it is stable across a name change,
        a marriage, or a new email address. Email is the fallback for the many exports that carry
        no identifier at all.
        """
        if parsed.external_id:
            existing = self.patients.get_by_external_id(organization_id, parsed.external_id)
            if existing is not None:
                return existing

        return self.patients.get_by_email(organization_id, parsed.email)

    # -----------------------------------------------------------------------------------------
    # Commit
    # -----------------------------------------------------------------------------------------

    def commit(
        self,
        organization_id: uuid.UUID,
        preview: ImportPreview,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> ImportResult:
        """Write the valid rows.

        Does **not** commit the transaction — the caller owns that boundary, which is what makes
        SPEC §7.1's "single transaction, no half-imported state" guarantee hold. If anything below
        raises, the router's session rolls back and the practice's data is exactly as it was.
        """
        result = ImportResult()

        for parsed in preview.rows:
            existing = self._find_existing(organization_id, parsed)

            if existing is None:
                patient = Patient(
                    first_name=parsed.first_name,
                    last_name=parsed.last_name,
                    email=parsed.email,
                    phone=parsed.phone,
                    external_id=parsed.external_id,
                    last_annual_visit_date=parsed.last_annual_visit_date,
                    # A placeholder: apply_derived_fields overwrites it immediately. The column is
                    # NOT NULL, so it needs *a* value before the object is valid.
                    next_annual_due_date=parsed.last_annual_visit_date,
                )
                self.patients.create(organization_id, patient)
                result.created += 1
            else:
                patient = existing
                patient.first_name = parsed.first_name
                patient.last_name = parsed.last_name
                patient.email = parsed.email
                if parsed.phone:
                    patient.phone = parsed.phone
                if parsed.external_id:
                    patient.external_id = parsed.external_id

                # Only ever move a visit date forward. A re-import of an older export must not
                # rewind a patient whose visit was recorded in the app since — that would make
                # them overdue again and send a reminder to someone who was seen last week.
                if parsed.last_annual_visit_date > patient.last_annual_visit_date:
                    patient.last_annual_visit_date = parsed.last_annual_visit_date

                result.updated += 1

            # Derived fields through RecallService, never computed here. SPEC §5.1 allows exactly
            # one writer of status and due date, and this is not it.
            self.recall.apply_derived_fields(organization_id, patient)

        result.skipped = len(preview.problems)

        # One summary event rather than one per patient. Importing 5,000 patients must not bury
        # the activity feed under 5,000 identical lines (SPEC §7.1).
        self.activity.record(
            organization_id,
            event_type=ActivityEventType.PATIENT_IMPORTED,
            actor_user_id=actor_user_id,
            payload={
                "total_rows": preview.total_rows,
                "created": result.created,
                "updated": result.updated,
                "skipped": result.skipped,
                "imported_at": datetime.now(UTC).isoformat(),
            },
        )

        logger.info(
            "Import for organization %s: %d created, %d updated, %d skipped",
            organization_id,
            result.created,
            result.updated,
            result.skipped,
        )
        return result

    # -----------------------------------------------------------------------------------------
    # Error report (SPEC §7.1)
    # -----------------------------------------------------------------------------------------

    @staticmethod
    def build_error_report(preview: ImportPreview) -> str:
        """Render the rejected rows as a CSV the practice can open and work through.

        SPEC §7.1 asks for "original row number, the offending value, and a plain-English reason",
        which is exactly the shape someone needs to sit with their export open in Excel and fix
        it. A list of errors on screen is unusable for 200 rows; a file they can sort is not.
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["row_number", "problem", "column", "value", "what_to_do"])

        for problem in sorted(preview.problems, key=lambda item: item.row_number):
            writer.writerow(
                [
                    problem.row_number,
                    problem.category.replace("_", " "),
                    problem.column,
                    problem.value,
                    problem.reason,
                ]
            )

        return buffer.getvalue()


class _ByteLimitedStream(io.RawIOBase):
    """Wraps a stream and refuses to read past a byte limit.

    Needed because the ``Content-Length`` check in middleware can be sidestepped by a chunked
    upload, which declares no length at all. Counting while reading is the only bound that always
    applies.
    """

    def __init__(self, wrapped: io.BufferedIOBase, limit: int) -> None:
        self._wrapped = wrapped
        self._limit = limit
        self._read_so_far = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        chunk = self._wrapped.read(len(buffer))
        if not chunk:
            return 0

        self._read_so_far += len(chunk)
        if self._read_so_far > self._limit:
            raise CsvImportError(
                f"That file is larger than {self._limit // (1024 * 1024)} MB. "
                "Please split it into smaller files."
            )

        buffer[: len(chunk)] = chunk
        return len(chunk)
