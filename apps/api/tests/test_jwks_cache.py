"""JWKS caching and the bounded retry (SPEC §3.2).

The requirement is "a cached key set (refresh on kid miss, bounded retry)". Each clause guards
against a different failure, and the tests below check them separately:

* **cached** — so verification is not an HTTP round trip per request;
* **refresh on kid miss** — so a key rotation does not lock everyone out until a restart;
* **bounded retry** — so a stream of bogus ``kid`` values cannot turn our verification path into
  a request amplifier aimed at the auth server.

Every test here counts fetches. That is the only way to tell caching from re-fetching, since both
produce a correct answer.
"""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.jwks import JWKSCache, JWKSError


def make_jwks(kid: str) -> dict[str, Any]:
    """A one-key JWKS document with the given ``kid``."""
    key = ec.generate_private_key(ec.SECP256R1())
    entry = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    entry.update({"kid": kid, "use": "sig", "alg": "ES256"})
    return {"keys": [entry]}


class FetchCounter:
    """Stands in for the network, and records how often it was asked.

    ``documents`` is a list: each fetch consumes the next one, so a test can simulate a key
    rotation by queuing a second document. The last entry repeats once exhausted.
    """

    def __init__(self, *documents: dict[str, Any]) -> None:
        self.documents = list(documents)
        self.calls = 0
        self.should_fail = False

    def install(self, cache: JWKSCache, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        from jwt import PyJWK

        counter = self

        def fake_fetch(self: JWKSCache) -> None:
            counter.calls += 1
            if counter.should_fail:
                if self._keys:
                    return  # matches the real behaviour: keep the cache on a failed refresh
                raise JWKSError("simulated fetch failure")

            index = min(counter.calls - 1, len(counter.documents) - 1)
            document = counter.documents[index]
            self._keys = {e["kid"]: PyJWK.from_dict(e) for e in document["keys"]}
            self._fetched_at = time.monotonic()

        monkeypatch.setattr(cache, "_fetch", fake_fetch.__get__(cache, JWKSCache))


@pytest.fixture
def cache() -> JWKSCache:
    return JWKSCache(
        "http://test.invalid/jwks.json", ttl_seconds=600.0, min_refresh_interval_seconds=10.0
    )


# ---------------------------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------------------------


def test_the_first_lookup_fetches(cache: JWKSCache, monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    assert cache.get_key("key-1") is not None
    assert fetcher.calls == 1


def test_subsequent_lookups_are_served_from_cache(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the cache. Without this, every authenticated request is two round trips."""
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    for _ in range(50):
        cache.get_key("key-1")

    assert fetcher.calls == 1


def test_an_expired_ttl_triggers_a_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a known key is refetched once the cache is stale, so rotations are picked up."""
    cache = JWKSCache(
        "http://test.invalid/jwks.json", ttl_seconds=0.0, min_refresh_interval_seconds=0.0
    )
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("key-1")
    cache.get_key("key-1")

    assert fetcher.calls == 2


# ---------------------------------------------------------------------------------------------
# Refresh on kid miss
# ---------------------------------------------------------------------------------------------


def test_an_unknown_kid_triggers_a_refresh(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key rotation looks exactly like an unknown ``kid``.

    Supabase rotates its signing key; tokens start arriving with a ``kid`` we have never seen.
    Without this refresh, every user is locked out until the process restarts — and the symptom
    is "everyone is signed out", which reads like a far more alarming problem than it is.
    """
    fetcher = FetchCounter(make_jwks("old-key"), make_jwks("new-key"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("old-key")
    assert fetcher.calls == 1

    # The rotated key: unknown, so a refresh is attempted, and it succeeds.
    assert cache.get_key("new-key") is not None
    assert fetcher.calls == 2


def test_a_kid_that_is_never_published_raises(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    with pytest.raises(JWKSError, match="No signing key"):
        cache.get_key("never-published")


# ---------------------------------------------------------------------------------------------
# The bound
# ---------------------------------------------------------------------------------------------


def test_repeated_unknown_kids_do_not_cause_repeated_fetches(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BOUND (SPEC §3.2's "bounded retry").

    Without it, this loop would make 100 outbound HTTP requests to the auth server, triggered by
    100 unauthenticated requests carrying junk. That is a denial-of-service amplifier: cheap for
    the attacker, expensive for the auth server, and it would look like Supabase failing rather
    than like an attack on us.

    With the bound, the first miss refreshes and the remaining 99 are refused from cache.
    """
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("key-1")  # populate
    assert fetcher.calls == 1

    for index in range(100):
        with pytest.raises(JWKSError):
            cache.get_key(f"forged-kid-{index}")

    # One additional fetch for the first miss; the rest were inside the retry window.
    assert fetcher.calls == 2


def test_the_bound_lifts_once_the_interval_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound rate-limits refreshes; it does not disable them permanently."""
    cache = JWKSCache(
        "http://test.invalid/jwks.json", ttl_seconds=600.0, min_refresh_interval_seconds=0.0
    )
    fetcher = FetchCounter(make_jwks("key-1"), make_jwks("key-2"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("key-1")
    with pytest.raises(JWKSError):
        cache.get_key("key-2-not-yet")

    # With no minimum interval, the next miss refreshes again and finds the rotated key.
    assert cache.get_key("key-2") is not None


def test_an_empty_cache_always_attempts_a_fetch(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound must not apply when there is nothing cached.

    Otherwise a failed first fetch would leave the service unable to verify anything for the
    length of the interval — the bound would have caused the outage it exists to prevent.
    """
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.should_fail = True
    fetcher.install(cache, monkeypatch)

    with pytest.raises(JWKSError):
        cache.get_key("key-1")
    with pytest.raises(JWKSError):
        cache.get_key("key-1")

    assert fetcher.calls == 2


# ---------------------------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------------------------


def test_a_failed_refresh_keeps_the_existing_keys(
    cache: JWKSCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network blip must not sign everyone out.

    Stale keys keep verifying tokens correctly right up until an actual rotation, so continuing
    with them is strictly better than failing every request.
    """
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("key-1")
    fetcher.should_fail = True

    with pytest.raises(JWKSError):
        cache.get_key("unknown-kid")

    # The known key still works.
    assert cache.get_key("key-1") is not None


def test_clear_empties_the_cache(cache: JWKSCache, monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = FetchCounter(make_jwks("key-1"))
    fetcher.install(cache, monkeypatch)

    cache.get_key("key-1")
    assert cache.cached_kids == {"key-1"}

    cache.clear()
    assert cache.cached_kids == set()
