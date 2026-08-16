"""PII redaction in logs (SPEC §9).

    "No patient PII in application logs — add a log filter that redacts email/phone patterns
     and test it."

This file is the "and test it" half. The tests below capture real log output through the real
filter, because the failure mode being guarded against is subtle: a filter that cleans the format
string but not its arguments looks entirely correct in review, passes a naive test written with
f-strings, and leaks every single time the code uses lazy ``%s`` formatting — which is the style
everyone is told to use.

So each test asserts on the *formatted* record, exactly as it would be written to a file.
"""

from __future__ import annotations

import logging
import sys

import pytest

from app.core.logging import (
    EMAIL_REPLACEMENT,
    PHONE_REPLACEMENT,
    RedactingFormatter,
    RedactPatientDataFilter,
    redact,
)


@pytest.fixture
def captured_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """A logger with the redaction filter attached, capturing formatted output."""
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(RedactPatientDataFilter())
    return caplog


# ---------------------------------------------------------------------------------------------
# The pure function
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "sarah.johnson@example.com",
        "Contact sarah.johnson@example.com about the appointment",
        "SARAH.JOHNSON@EXAMPLE.COM",
        "first+tag@sub.domain.co.uk",
        "a@b.io",
        "user_name-123@example-clinic.org",
    ],
)
def test_email_addresses_are_removed(text: str) -> None:
    result = redact(text)

    assert "@" not in result or EMAIL_REPLACEMENT in result
    assert "example.com" not in result.replace(EMAIL_REPLACEMENT, "")


@pytest.mark.parametrize(
    "text",
    [
        "5551234567",
        "555-123-4567",
        "(555) 123-4567",
        "555.123.4567",
        "+1 555 123 4567",
        "+1-555-123-4567",
        "Call the patient on 555-123-4567 tomorrow",
    ],
)
def test_phone_numbers_are_removed(text: str) -> None:
    result = redact(text)

    assert PHONE_REPLACEMENT in result
    assert "1234567" not in result
    assert "123-4567" not in result


def test_both_are_removed_from_one_line() -> None:
    line = "Patient sarah.johnson@example.com / 555-123-4567 is overdue"

    result = redact(line)

    assert "sarah.johnson" not in result
    assert "555-123-4567" not in result
    assert "is overdue" in result  # the useful part survives


def test_an_email_containing_digits_is_redacted_as_one_unit() -> None:
    """Emails are handled first so a phone-shaped local part does not leave a mangled fragment."""
    result = redact("5551234567@example.com")

    assert result == EMAIL_REPLACEMENT


@pytest.mark.parametrize(
    "text",
    [
        "Processed 1234 patients",
        "Version 2.0.1 started",
        "Took 1234.5678 seconds",
        "Correlation id 3f2a91c4e8b7",
        "Imported 327 records, 320 ready",
    ],
)
def test_ordinary_numbers_survive(text: str) -> None:
    """Over-redaction has a cost too: logs that have eaten their own diagnostics are useless.

    The patterns are deliberately anchored so a row count, a duration, or a version number is not
    mistaken for a phone number.
    """
    assert redact(text) == text


# ---------------------------------------------------------------------------------------------
# The filter, through a real logger
# ---------------------------------------------------------------------------------------------


def test_lazy_formatting_arguments_are_redacted(captured_logs: pytest.LogCaptureFixture) -> None:
    """THE CASE THAT MATTERS MOST.

    ``logger.info("Sending to %s", email)`` is the recommended style, and the email is in
    ``record.args``, not in ``record.msg``. A filter that only cleaned ``msg`` would pass a test
    written with an f-string and leak here — on the code everyone is told to write.
    """
    logging.getLogger("test").info("Sending reminder to %s", "sarah.johnson@example.com")

    output = captured_logs.text
    assert "sarah.johnson@example.com" not in output
    assert EMAIL_REPLACEMENT in output


def test_eagerly_formatted_messages_are_redacted(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """The f-string case: the address is baked into ``record.msg``."""
    email = "michael.brennan@example.com"
    logging.getLogger("test").warning(f"Delivery failed for {email}")

    assert email not in captured_logs.text
    assert EMAIL_REPLACEMENT in captured_logs.text


def test_dict_style_arguments_are_redacted(captured_logs: pytest.LogCaptureFixture) -> None:
    logging.getLogger("test").info("Reminder to %(email)s", {"email": "jennifer.tran@example.com"})

    assert "jennifer.tran@example.com" not in captured_logs.text


def test_multiple_arguments_are_all_redacted(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("test").info(
        "Patient %s at %s could not be reached",
        "david.okafor@example.com",
        "555-987-6543",
    )

    output = captured_logs.text
    assert "david.okafor" not in output
    assert "987-6543" not in output


def test_non_string_arguments_pass_through_unharmed(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """Redaction must not corrupt counts, durations, or identifiers."""
    logging.getLogger("test").info("Processed %d patients in %.2fs", 55, 1.23)

    assert "Processed 55 patients in 1.23s" in captured_logs.text


def test_exception_tracebacks_are_redacted() -> None:
    """A common real-world leak, and the reason redaction happens at the formatter.

    A database integrity error quotes the row that violated the constraint — email column and
    all — so ``logger.exception("Import failed")`` writes a patient's address into the log
    without anyone having written the word "email" anywhere near it.

    This is asserted against the *formatter* rather than a captured handler because that is where
    the guarantee actually lives. Redacting the exception object instead would look correct and
    fail silently: once any handler has formatted the record, the rendered traceback is cached on
    ``record.exc_text`` and later edits to the exception have no effect.
    """
    formatter = RedactingFormatter("%(levelname)s %(message)s")

    try:
        raise ValueError(
            "duplicate key value violates unique constraint: "
            "Key (email)=(maria.castillo@example.com) already exists."
        )
    except ValueError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Import failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    output = formatter.format(record)

    assert "maria.castillo@example.com" not in output
    assert EMAIL_REPLACEMENT in output
    # The diagnostic value survives — you can still see what went wrong and where.
    assert "unique constraint" in output
    assert "Traceback" in output


def test_the_formatter_redacts_phone_numbers_in_tracebacks() -> None:
    formatter = RedactingFormatter("%(message)s")

    try:
        raise RuntimeError("Could not reach patient on 555-123-4567")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Send failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    output = formatter.format(record)

    assert "555-123-4567" not in output
    assert PHONE_REPLACEMENT in output


def test_the_filter_never_drops_records(captured_logs: pytest.LogCaptureFixture) -> None:
    """It edits records; it must not swallow them.

    A filter that returned False on a match would silently discard exactly the log lines most
    worth having — the ones about a specific patient going wrong.
    """
    logger = logging.getLogger("test")
    logger.info("Line one with sarah@example.com")
    logger.info("Line two with no contact details")
    logger.info("Line three with 555-111-2222")

    assert len(captured_logs.records) == 3


def test_a_realistic_reminder_log_line_is_safe(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """End to end, on the shape of line the reminder job will actually write."""
    logging.getLogger("app.services.reminders").info(
        "Reminder %s sent to %s (%s) for due date %s",
        "T_MINUS_7",
        "Sarah Johnson",
        "sarah.johnson@example.com",
        "2026-07-01",
    )

    output = captured_logs.text
    assert "sarah.johnson@example.com" not in output
    # The operationally useful parts survive.
    assert "T_MINUS_7" in output
    assert "2026-07-01" in output
