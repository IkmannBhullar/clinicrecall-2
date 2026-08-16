"""JWKS key cache — the public keys used to verify Supabase access tokens.

Supabase signs access tokens with a private key it never shares, and publishes the matching
public keys as a JWKS document at ``/auth/v1/.well-known/jwks.json``. To verify a token we need
the public key whose ``kid`` matches the token's header.

Fetching that document on every request would be absurd — it changes about as often as a
certificate does — so it is cached. The whole design question is *when to refetch*, and getting
that wrong causes one of two failures:

* **Refetch too rarely** and a key rotation locks every user out until the process restarts.
* **Refetch on every cache miss** and anyone can force unlimited outbound requests to the auth
  server simply by sending tokens with random ``kid`` values.

SPEC §3.2 asks for "a cached key set (refresh on kid miss, bounded retry)", which is the balance
between those. This module implements it:

* keys are cached for a TTL and refreshed when it expires;
* an unknown ``kid`` triggers an immediate refresh, because that is what a rotation looks like;
* but refreshes are rate-limited, so a stream of bogus ``kid`` values cannot become a traffic
  amplifier pointed at the auth server.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from jwt import PyJWK

logger = logging.getLogger(__name__)


class JWKSError(Exception):
    """Raised when a usable signing key cannot be obtained.

    Deliberately distinct from a token being *invalid*: this means we could not check, which is
    an availability problem on our side, not a bad request from the caller.
    """


class JWKSCache:
    """Fetches and caches JWKS signing keys, keyed by ``kid``.

    Thread-safe: FastAPI runs synchronous endpoints in a thread pool, so several requests can ask
    for a key at the same moment. A lock around the refresh means a rotation triggers one fetch
    rather than one per in-flight request.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        ttl_seconds: float = 600.0,
        min_refresh_interval_seconds: float = 10.0,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        """
        :param jwks_url: where to fetch the key set from
        :param ttl_seconds: how long a fetched key set is considered fresh (10 minutes)
        :param min_refresh_interval_seconds: the bound on retries. However many unknown ``kid``
            values arrive, at most one refresh happens in this window. This is what stops a
            hostile client turning our verification path into a request amplifier.
        :param request_timeout_seconds: kept short — a hanging auth server must not hang every
            request waiting on it.
        """
        self.jwks_url = jwks_url
        self.ttl_seconds = ttl_seconds
        self.min_refresh_interval_seconds = min_refresh_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds

        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

        # When a refresh was last attempted *on an already-populated cache*.
        #
        # Only those attempts count towards the bound. Tracking every fetch here — including the
        # initial one that populates an empty cache — would mean the very first kid miss after
        # startup falls inside the retry window and is refused, which defeats the whole point of
        # refreshing on a miss.
        #
        # Starts at -inf so the first miss is always allowed, regardless of what value
        # `time.monotonic()` happens to start from on this platform.
        self._last_refresh_attempt_at: float = float("-inf")

    # -----------------------------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------------------------

    def get_key(self, kid: str) -> PyJWK:
        """Return the public key with this ``kid``, refreshing the cache if necessary.

        :raises JWKSError: if no such key can be obtained
        """
        # Fast path: a fresh cache containing the key. No lock needed — dict reads are atomic
        # under the GIL and a slightly stale read simply falls through to the slow path.
        key = self._keys.get(kid)
        if key is not None and not self._is_stale():
            return key

        with self._lock:
            # Re-check inside the lock: another thread may have refreshed while we waited, in
            # which case there is nothing left to do.
            key = self._keys.get(kid)
            if key is not None and not self._is_stale():
                return key

            self._refresh_if_allowed()

            key = self._keys.get(kid)
            if key is None:
                raise JWKSError(
                    f"No signing key with kid={kid!r} is published at {self.jwks_url}. "
                    "The token was signed by a key this server does not recognise."
                )
            return key

    def clear(self) -> None:
        """Discard all cached keys. Used by tests, and after a configuration change."""
        with self._lock:
            self._keys = {}
            self._fetched_at = 0.0
            self._last_refresh_attempt_at = float("-inf")

    @property
    def cached_kids(self) -> set[str]:
        """The ``kid`` values currently cached. Exposed for diagnostics and tests."""
        return set(self._keys)

    # -----------------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------------

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self.ttl_seconds

    def _refresh_if_allowed(self) -> None:
        """Refetch the key set, unless we tried too recently.

        THE BOUND (SPEC §3.2's "bounded retry"). Without it, a client sending tokens with random
        ``kid`` values would cause one outbound HTTP request per token — turning our token
        verification into a denial-of-service tool aimed at the auth server, using nothing but
        unauthenticated requests.
        """
        # An empty cache always tries, unconditionally. If the bound applied here, a single
        # failed fetch at startup would leave the service unable to verify anything for the
        # length of the interval — the bound would have caused the outage it exists to prevent.
        if not self._keys:
            self._fetch()
            return

        now = time.monotonic()
        since_last_attempt = now - self._last_refresh_attempt_at

        if since_last_attempt < self.min_refresh_interval_seconds:
            logger.debug(
                "Skipping JWKS refresh: last attempt was %.1fs ago (minimum interval %.1fs)",
                since_last_attempt,
                self.min_refresh_interval_seconds,
            )
            return

        self._last_refresh_attempt_at = now
        self._fetch()

    def _fetch(self) -> None:
        """Download and parse the key set.

        On failure, any previously cached keys are kept. A transient network blip should not sign
        every user out — stale keys still verify tokens correctly right up until a rotation.
        """
        try:
            response = httpx.get(self.jwks_url, timeout=self.request_timeout_seconds)
            response.raise_for_status()
            document: dict[str, Any] = response.json()
        except Exception as exc:
            if self._keys:
                logger.warning(
                    "JWKS refresh from %s failed (%s). Continuing with %d cached key(s).",
                    self.jwks_url,
                    exc,
                    len(self._keys),
                )
                return
            raise JWKSError(f"Could not fetch signing keys from {self.jwks_url}: {exc}") from exc

        keys: dict[str, PyJWK] = {}
        for entry in document.get("keys", []):
            kid = entry.get("kid")
            if not kid:
                # A key with no `kid` cannot be selected by a token header, so it is unusable.
                continue
            try:
                keys[kid] = PyJWK.from_dict(entry)
            except Exception as exc:
                # One malformed entry must not discard the rest of the key set.
                logger.warning("Skipping unusable JWKS entry kid=%s: %s", kid, exc)

        if not keys:
            if self._keys:
                logger.warning(
                    "JWKS document at %s contained no usable keys; keeping cache.", self.jwks_url
                )
                return
            raise JWKSError(f"JWKS document at {self.jwks_url} contained no usable keys.")

        self._keys = keys
        self._fetched_at = time.monotonic()
        logger.debug("Cached %d JWKS signing key(s) from %s", len(keys), self.jwks_url)
