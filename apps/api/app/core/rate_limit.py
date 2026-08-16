"""Rate limiting (SPEC §9).

    "rate limiting on auth, import, and send-reminder endpoints (slowapi — do not hand-roll)"

The instruction not to hand-roll is worth taking seriously. A naive counter-in-a-dict has at
least three problems that are not obvious until they bite: it never evicts, so it is a memory
leak; it is not safe across threads, so counts are lost under concurrency; and its window
boundary lets twice the intended traffic through at the moment the window rolls over.

Each limit below is a *specific* answer to a *specific* abuse, rather than a number chosen to
look prudent.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.errors import correlation_id_var

# Keyed by client IP.
#
# A signed-in user would be a better key, but the limits that matter most guard endpoints reached
# *before* identity is established — and a limiter that has to authenticate a request in order to
# decide whether to rate-limit it has already done the expensive work.
#
# Storage is in-process, which is right for a single-instance demo deployment. A multi-instance
# production deployment would point `storage_uri` at Redis; without that, each instance keeps its
# own counts and the effective limit is multiplied by the instance count.
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)


# ---------------------------------------------------------------------------------------------
# The limits
# ---------------------------------------------------------------------------------------------

#: Authentication-adjacent endpoints — anything that verifies a token or resolves an identity.
#: Generous enough for normal use, tight enough that credential stuffing is not viable at speed.
AUTH_LIMIT = "30/minute"

#: CSV import. Each request parses a file of up to 10 MB and writes in a single transaction, so
#: it is by far the most expensive thing the API does. A practice imports a file perhaps twice a
#: year; five per minute is already absurdly permissive for the real workload.
IMPORT_LIMIT = "5/minute"

#: Manual "Send Reminder". This one has a consequence outside the system — an email actually
#: reaches a patient — so the limit protects a person's inbox, not just the server. SPEC §6.2
#: additionally requires a per-patient limit of one send per hour, which is enforced in the
#: reminder service; this is the coarser per-client bound.
SEND_REMINDER_LIMIT = "20/minute"

#: Ordinary reads. High enough to be invisible to a real user, low enough to stop a script.
READ_LIMIT = "120/minute"

#: Admin demo utilities (reset data, run the reminder job). Each does substantial work, and
#: nobody legitimately needs to trigger one more than a few times a minute.
ADMIN_UTILITY_LIMIT = "10/minute"


# ---------------------------------------------------------------------------------------------
# REQUIRED SIGNATURE for any endpoint carrying @limiter.limit(...)
#
#     def my_endpoint(request: Request, response: Response, ...) -> ...:
#
# Both parameters must be present, even when the body uses neither:
#
#   * `request`  — slowapi reads the client address from it to key the limit. Without it,
#                  the decorator raises at request time rather than at import time, so the
#                  mistake surfaces as a 500 on a live route instead of a startup failure.
#   * `response` — because `headers_enabled=True` above, slowapi writes X-RateLimit-Limit,
#                  X-RateLimit-Remaining and X-RateLimit-Reset into it. If the endpoint returns
#                  a Pydantic model and has no `response` parameter, slowapi has nowhere to put
#                  them and raises "parameter `response` must be an instance of
#                  starlette.responses.Response".
#
# Those headers are worth the two extra parameters: they let a client back off deliberately
# rather than discover the limit by being refused.
RATE_LIMITED_ENDPOINT_SIGNATURE = "(request: Request, response: Response, ...)"


async def handle_rate_limit_exceeded(request: Request, exc: Exception) -> JSONResponse:
    """Return the standard error envelope for a throttled request.

    slowapi's own handler returns a plain-text body, which would be the one response in the API
    with a different shape — so the frontend would need a special case for exactly the situation
    where it is least likely to be tested.
    """
    assert isinstance(exc, RateLimitExceeded)

    response = JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Too many requests. Please wait a moment and try again.",
                "correlation_id": correlation_id_var.get(),
            }
        },
    )

    # Tell a well-behaved client how long to wait, rather than leaving it to guess or spin.
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)

    return response


def register_rate_limiting(app: FastAPI) -> None:
    """Attach the limiter to the application. Called once, from ``app.main``."""
    # slowapi reads the limiter off app.state; without this, every @limiter.limit decorator
    # raises at request time rather than at startup.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)
