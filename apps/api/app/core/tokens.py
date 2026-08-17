"""Signed unsubscribe tokens (SPEC §6.5).

Every reminder email carries a one-click unsubscribe link. That link is reached by someone who
is **not signed in** — a patient, on their phone, from an email — so the URL itself has to prove
it was issued by us.

The naive version is ``/unsubscribe/{public_id}``. It fails badly: anyone can walk the identifier
space and opt out patients who never asked to be, and since ``opted_out_at`` is honoured
permanently that is a quiet, hard-to-notice denial of service against a clinic's entire recall
list.

So the link carries an HMAC over the patient's public id::

    /unsubscribe/{public_id}.{signature}

Forging one requires the signing secret, which lives only in the server's environment.

**No expiry, deliberately.** A patient who finds a two-year-old reminder in their inbox and wants
to stop hearing from the practice must be able to. An expired opt-out link that says "this link
is no longer valid" is worse than useless — it is a consent mechanism that stops working
precisely when someone tries to use it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from app.core.config import settings

#: Length of the base64url-encoded signature kept in the URL.
#:
#: Truncating SHA-256 to 128 bits leaves an attacker needing about 2^128 guesses against a
#: rate-limited endpoint, while keeping the link short enough to sit in an email without
#: wrapping. Full-length would be 43 characters of noise for no practical gain.
SIGNATURE_LENGTH = 22


class InvalidUnsubscribeTokenError(Exception):
    """The token is malformed or its signature does not match."""


def _sign(public_id: str) -> str:
    """Return the base64url signature for a patient's public id."""
    if not settings.unsubscribe_token_secret:
        # Failing loudly beats issuing links signed with an empty key, which would be forgeable
        # by anyone who noticed.
        raise RuntimeError(
            "UNSUBSCRIBE_TOKEN_SECRET is not set. Unsubscribe links cannot be signed. "
            "Run `make setup`, which generates one."
        )

    digest = hmac.new(
        settings.unsubscribe_token_secret.encode(),
        public_id.encode(),
        hashlib.sha256,
    ).digest()

    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:SIGNATURE_LENGTH]


def make_unsubscribe_token(public_id: str) -> str:
    """Build the signed token for a patient's unsubscribe link."""
    return f"{public_id}.{_sign(public_id)}"


def make_unsubscribe_url(public_id: str) -> str:
    """The absolute URL to put in an email."""
    return f"{settings.api_base_url}/unsubscribe/{make_unsubscribe_token(public_id)}"


def verify_unsubscribe_token(token: str) -> str:
    """Check a token and return the patient public id it refers to.

    :raises InvalidUnsubscribeTokenError: if the token is malformed or the signature does not match
    """
    if not token or "." not in token:
        raise InvalidUnsubscribeTokenError("Unsubscribe link is malformed.")

    # rsplit, not split: a public id never contains a dot today, but if that ever changed a
    # left-split would silently start verifying the wrong substring.
    public_id, _, signature = token.rpartition(".")

    if not public_id or not signature:
        raise InvalidUnsubscribeTokenError("Unsubscribe link is malformed.")

    expected = _sign(public_id)

    # Constant-time comparison. A normal `==` returns as soon as two bytes differ, so the time it
    # takes leaks how much of a guess was correct — which is enough to reconstruct a valid
    # signature one character at a time.
    if not hmac.compare_digest(signature, expected):
        raise InvalidUnsubscribeTokenError("Unsubscribe link is not valid.")

    return public_id


def verify_job_token(provided: str | None) -> bool:
    """Check the ``X-Job-Token`` header on the reminder job endpoint (SPEC §6.3).

    Constant-time, for the same reason as above: a shared secret compared with ``==`` can be
    recovered a byte at a time by timing the responses.
    """
    if not provided or not settings.job_token:
        return False

    return hmac.compare_digest(provided, settings.job_token)
