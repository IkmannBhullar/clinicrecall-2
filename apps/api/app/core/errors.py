"""Error handling — one envelope, stable codes, and a correlation ID (SPEC §9).

Every error this API returns has the same shape::

    {
      "error": {
        "code": "PATIENT_NOT_FOUND",
        "message": "That patient could not be found.",
        "correlation_id": "3f2a91c4e8b7"
      }
    }

Three properties, each of which exists for a specific reason.

**A stable ``code``.** The frontend branches on this, never on the message. Messages get reworded;
a code that client logic depends on must not.

**A safe ``message``.** Written for a clinic receptionist, and it never contains a stack trace, a
SQL fragment, a file path, or an internal identifier. An unhandled exception produces a generic
message and nothing else — the real detail is logged server-side against the correlation ID.

**A ``correlation_id``.** The bridge between the two. When someone says "it went wrong", that
short string is enough to find the exact traceback in the logs, without the traceback ever having
been shown to them.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# The correlation ID for the request currently being handled.
#
# A ContextVar rather than a global: FastAPI serves requests concurrently, and a plain module
# variable would let one request's identifier leak into another's error response — which is
# exactly the kind of bug that makes a log trail untrustworthy.
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """A short, unique, human-readable request identifier.

    Twelve hex characters rather than a full UUID: someone has to be able to read it off a screen
    and type it into a search box, and 48 bits is far more than enough to distinguish the
    requests in any realistic log window.
    """
    return uuid.uuid4().hex[:12]


def current_correlation_id() -> str:
    return correlation_id_var.get()


# ---------------------------------------------------------------------------------------------
# Application errors
# ---------------------------------------------------------------------------------------------


class AppError(Exception):
    """Base class for errors this application raises deliberately.

    Anything inheriting from this is a *known* failure with a message that is safe to show. That
    is the distinction that matters: an ``AppError`` reaches the client with its own message; any
    other exception is treated as a bug and its details are withheld.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "Something went wrong."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        super().__init__(self.message)


class NotFoundError(AppError):
    """The requested thing does not exist — or belongs to another practice.

    Those two cases return the *same* response on purpose. Distinguishing them would let someone
    holding a patient identifier from another organization learn that it is real (SPEC §3.2).
    """

    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    message = "That record could not be found."


class UnauthorizedError(AppError):
    """No valid credentials were supplied."""

    code = "UNAUTHORIZED"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "You need to sign in to do that."


class ForbiddenError(AppError):
    """Authenticated, but not permitted — typically a staff user attempting an admin action."""

    code = "FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN
    message = "You do not have permission to do that."


class ValidationFailedError(AppError):
    """The request was well-formed but its contents are not acceptable."""

    code = "VALIDATION_FAILED"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    message = "Some of the information supplied is not valid."


class ConflictError(AppError):
    """The request collides with the current state — a duplicate, or a lost update."""

    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT
    message = "That conflicts with something that already exists."


class ServiceUnavailableError(AppError):
    """A dependency this request needs is not reachable.

    Distinct from a 500: nothing is *wrong*, something is *down*, and retrying may well work.
    Used when the JWKS endpoint cannot be reached — we cannot verify the token, but the token may
    be perfectly valid, so answering 401 would be a lie.
    """

    code = "SERVICE_UNAVAILABLE"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "A service this request depends on is temporarily unavailable. Please try again."


# ---------------------------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------------------------


def error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    details: object | None = None,
) -> JSONResponse:
    """Build the standard error envelope."""
    body: dict[str, object] = {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": current_correlation_id(),
        }
    }

    # `details` carries structured, already-safe information — for example the per-row problems
    # from a CSV import. It is never populated from an exception.
    if details is not None:
        body["error"]["details"] = details  # type: ignore[index]

    return JSONResponse(
        status_code=status_code,
        content=body,
        headers={"X-Correlation-ID": current_correlation_id()},
    )


# ---------------------------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------------------------


async def handle_app_error(_request: Request, exc: Exception) -> JSONResponse:
    """Errors we raised on purpose. The message is safe by construction."""
    assert isinstance(exc, AppError)
    logger.info("Handled %s: %s", exc.code, exc.message)
    return error_response(code=exc.code, message=exc.message, status_code=exc.status_code)


async def handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    """Reshape FastAPI's own HTTPExceptions into our envelope.

    Without this, a 404 from routing and a 404 from application logic would have different shapes,
    and the frontend would need two code paths for the same situation.
    """
    assert isinstance(exc, StarletteHTTPException)
    code = {
        status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "REQUEST_TOO_LARGE",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    }.get(exc.status_code, "HTTP_ERROR")

    return error_response(
        code=code,
        message=str(exc.detail) if exc.detail else "Request failed.",
        status_code=exc.status_code,
    )


async def handle_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """Pydantic rejected the request body or query parameters.

    The field-level detail is included because it is genuinely useful and contains only what the
    caller already sent us — but it is rewritten into a small, flat shape rather than passed
    through, since Pydantic's raw output includes internal context and occasionally the offending
    input value itself.
    """
    assert isinstance(exc, RequestValidationError)

    details = [
        {
            # Drop the leading "body"/"query" segment: the caller knows where they put it.
            "field": ".".join(str(part) for part in error["loc"][1:]) or "request",
            "problem": error["msg"],
        }
        for error in exc.errors()
    ]

    return error_response(
        code="VALIDATION_FAILED",
        message="Some of the information supplied is not valid.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details=details,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug. The client learns nothing about it beyond a correlation ID.

    This is the handler that matters most for SPEC §9's "stack traces never reach the client".
    A traceback tells an attacker the framework, the file layout, the ORM, and often a SQL
    fragment — while telling the receptionist reading it precisely nothing.

    So the full exception goes to the server log, tagged with the correlation ID, and the client
    gets a generic message plus that ID. Someone reporting "it broke" can quote twelve characters
    and an engineer can find the exact traceback.
    """
    logger.exception(
        "Unhandled error [correlation_id=%s] on %s %s",
        current_correlation_id(),
        request.method,
        request.url.path,
    )

    message = "Something went wrong. Please try again."
    if not settings.is_production:
        # In development, name the exception type — enough to orient, still no traceback.
        message = f"{message} ({type(exc).__name__}: {exc})"

    return error_response(
        code="INTERNAL_ERROR",
        message=message,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install every handler. Called once, from ``app.main``."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    # The catch-all must be registered last, and against bare Exception, so that anything not
    # matched above cannot escape as an unformatted 500 with a traceback in the body.
    app.add_exception_handler(Exception, handle_unexpected_error)
