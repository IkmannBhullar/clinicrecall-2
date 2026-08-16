"""Logging, with patient contact details stripped out (SPEC §9).

    "No patient PII in application logs — add a log filter that redacts email/phone patterns
     and test it."

Why a filter rather than care at each call site. Logs leak by accident, not by design. Nobody
writes ``logger.info(patient.email)`` on purpose — it arrives when someone logs a whole exception
whose message happens to quote a row, or logs a request body while debugging, or adds
``%r`` on an object whose ``__repr__`` was later extended. Every one of those is a small,
reasonable-looking change, and each one silently writes a patient's email address into a file
that will be shipped to a log aggregator and kept for a year.

A filter on the logging pipeline catches all of them, including the ones nobody has written yet.

**This is a safety net, not a licence.** Deliberately logging patient data and relying on the
filter to clean it up would be a poor trade: redaction is pattern-based and cannot recognise a
name.
"""

from __future__ import annotations

import logging
import re

from app.core.config import settings

# ---------------------------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------------------------

#: Email addresses. Intentionally broad — over-redacting a log line costs nothing, while missing
#: one costs a patient's contact details.
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

#: Phone numbers in the shapes people actually write them:
#: 5551234567 · 555-123-4567 · (555) 123-4567 · +1 555 123 4567 · 555.123.4567
PHONE_PATTERN = re.compile(
    r"""
    (?<![\w.])                 # not mid-word, and not part of a decimal or version number
    (?:\+\d{1,3}[\s.\-]?)?     # optional country code
    (?:\(\d{3}\)|\d{3})        # area code, bracketed or bare
    [\s.\-]?                   # separator
    \d{3}                      # exchange
    [\s.\-]?                   # separator
    \d{4}                      # line number
    (?![\w.])                  # not followed by more digits
    """,
    re.VERBOSE,
)

EMAIL_REPLACEMENT = "[email redacted]"
PHONE_REPLACEMENT = "[phone redacted]"


def redact(text: str) -> str:
    """Remove email addresses and phone numbers from a string.

    Order matters slightly: emails are redacted first, so that a phone number appearing inside an
    email address (``5551234567@example.com``) is removed as part of the address rather than
    leaving a mangled fragment behind.
    """
    text = EMAIL_PATTERN.sub(EMAIL_REPLACEMENT, text)
    return PHONE_PATTERN.sub(PHONE_REPLACEMENT, text)


class RedactPatientDataFilter(logging.Filter):
    """Strips contact details from every log record passing through it.

    Both halves of a record have to be handled, which is easy to get wrong:

    * ``record.msg``  — the format string, e.g. ``"Sending to %s"``
    * ``record.args`` — the values substituted into it, which is where the email actually is

    A filter that only cleaned ``msg`` would look correct in review, pass a naive test using
    ``logger.info("...%s" % email)``, and leak every single time the code used lazy formatting —
    which is the style everyone is told to use.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact(value) if isinstance(value, str) else value for value in record.args
                )

        # Note what is deliberately NOT attempted here: rewriting the attached exception.
        #
        # Mutating `record.exc_info[1].args` looks like it should redact a traceback, and it does
        # not reliably work — once any handler has formatted the record, the rendered traceback is
        # cached on `record.exc_text` and later edits to the exception object have no effect. A
        # filter that tried would appear to work in isolation and leak whenever two handlers were
        # attached, which is the normal case under uvicorn and under pytest.
        #
        # Tracebacks are handled by RedactingFormatter below, which redacts the fully rendered
        # line and therefore cannot be defeated by caching.

        # Always returns True: this filter edits records, it does not drop them.
        return True


class RedactingFormatter(logging.Formatter):
    """Formats a record, then strips contact details from the finished line.

    This is the authoritative redaction point, and it is deliberately the *last* thing to run.
    By the time it sees the text, everything is included — the message, its arguments, and the
    full exception traceback.

    Tracebacks are the leak route that matters in practice. A database integrity error quotes the
    row that violated the constraint, email column and all::

        duplicate key value violates unique constraint
        Key (email)=(maria.castillo@example.com) already exists.

    Nobody wrote the word "email" anywhere near that log call. It arrives from
    ``logger.exception("Import failed")``, which is exactly what a careful engineer would write.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# ---------------------------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------------------------


def configure_logging() -> None:
    """Configure application logging and install the redaction filter.

    The filter is attached to the *handlers* rather than to individual loggers. A filter on a
    logger only sees records created through that logger; a filter on a handler sees everything
    written through it, including records from third-party libraries such as SQLAlchemy — which
    is precisely where an unexpected patient email is most likely to surface.
    """
    redaction_filter = RedactPatientDataFilter()

    handler = logging.StreamHandler()
    # Both layers, on purpose. The filter cleans the record itself, so any *other* handler
    # attached later also benefits. The formatter cleans the finished line, which is the only
    # way to reach a rendered traceback.
    handler.setFormatter(RedactingFormatter("%(levelname)-8s %(name)s: %(message)s"))
    handler.addFilter(redaction_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Uvicorn installs its own handlers at import time; they need the filter too, or every
    # request line and every startup message bypasses it.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for existing in logging.getLogger(name).handlers:
            existing.addFilter(redaction_filter)

    # SQLAlchemy at INFO echoes every statement, parameters included. That is a firehose of
    # patient data straight into the log, so it stays at WARNING unless explicitly debugging.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper so modules do not each import ``logging``."""
    return logging.getLogger(name)
