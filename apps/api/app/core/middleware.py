"""HTTP middleware: correlation IDs, security headers, and a request size limit (SPEC §9).

Order matters and is set in ``app.main``. Starlette runs middleware in reverse registration
order on the way in, so the correlation-ID middleware is registered last in order to run first —
every log line and error response produced by anything inside it then carries an identifier.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_413_REQUEST_ENTITY_TOO_LARGE

from app.core.errors import correlation_id_var, new_correlation_id

logger = logging.getLogger(__name__)

#: Largest request body the API will accept, in bytes.
#:
#: 10 MB comfortably holds the largest realistic patient CSV — the demo's messy sample is 327
#: rows and a few tens of kilobytes, and a large practice exporting 50,000 patients is still
#: well under this. The point of the cap is that an unbounded upload lets one request exhaust
#: server memory, and that requires no authentication to attempt.
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024

RequestHandler = Callable[[Request], Awaitable[Response]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Give every request a short identifier, and put it on the response.

    This is what makes SPEC §9's error contract work. The client is shown a correlation ID and
    nothing else; the server logs the full traceback against that same ID. Someone reporting a
    problem quotes twelve characters, and an engineer finds the exact failure — without a stack
    trace ever having been rendered in a browser.

    An inbound ``X-Correlation-ID`` is honoured so a trace can span the frontend and the API,
    but it is length-capped: the value is echoed into a response header and written to logs, and
    accepting an unbounded client-controlled string in either place is asking for trouble.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        inbound = request.headers.get("X-Correlation-ID", "")
        correlation_id = inbound[:64] if inbound else new_correlation_id()

        token = correlation_id_var.set(correlation_id)
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Correlation-ID"] = correlation_id

        # Method, path, status, duration — deliberately no query string and no body. Query
        # strings are where a search term (which may be a patient's name) would end up in a log.
        logger.info(
            "%s %s -> %d (%.1fms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            correlation_id,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive headers to every API response.

    The web app sets its own richer set (including a Content-Security-Policy) in
    ``next.config.mjs``. These are the API's own, and they matter for the same reasons even
    though the API serves JSON rather than HTML: a browser that can be persuaded to treat a JSON
    response as a document is the starting point for several old but entirely functional attacks.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)

        # Do not let a browser second-guess our declared content type.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # This API has no UI, so it never legitimately appears in a frame.
        response.headers.setdefault("X-Frame-Options", "DENY")

        # Patient identifiers appear in paths; keep them out of Referer headers sent elsewhere.
        response.headers.setdefault("Referrer-Policy", "same-origin")

        # A minimal CSP. The API returns JSON, so nothing should ever load from it.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )

        # Inert over plain HTTP on localhost; required once deployed behind TLS.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
        )

        # API responses are per-user and must never be held by a shared cache.
        response.headers.setdefault("Cache-Control", "no-store")

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies before they are read into memory (SPEC §9).

    Checks ``Content-Length`` and refuses early. That is cheap and catches the honest case — a
    user selecting a 2 GB file by mistake — without reading a byte of it.

    It is not a complete defence: a chunked request sends no ``Content-Length``, so the limit
    cannot be enforced from the header alone. The CSV importer therefore also counts bytes as it
    streams (phase 6). This middleware is the first line, not the only one.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        content_length = request.headers.get("content-length")

        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = 0

            if declared_bytes > MAX_REQUEST_BODY_BYTES:
                limit_mb = MAX_REQUEST_BODY_BYTES // (1024 * 1024)
                logger.warning(
                    "Rejected oversized request: %d bytes on %s %s",
                    declared_bytes,
                    request.method,
                    request.url.path,
                )
                # Built here rather than raised, because an exception from middleware does not
                # pass through the application's exception handlers — so the envelope has to be
                # constructed directly to keep the response shape consistent.
                return JSONResponse(
                    status_code=HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": (
                                f"That file is too large. The maximum upload size is {limit_mb} MB."
                            ),
                            "correlation_id": correlation_id_var.get(),
                        }
                    },
                )

        return await call_next(request)
