"""Access token verification (SPEC §3.2).

These tests mint their own tokens with a locally generated ES256 key pair and serve a matching
JWKS document from a stub. That makes them hermetic — no Supabase, no network, no clock
dependency — while exercising exactly the code path a real request takes.

Minting tokens locally is also the only way to test the cases that matter most. A real Supabase
instance will never hand you a token signed with the wrong key, or one with a missing ``exp``, or
one whose ``alg`` has been tampered with. Those are the tokens an attacker sends, and they are
precisely what verification exists to reject.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.config import settings
from app.core.jwks import JWKSCache, JWKSError
from app.core.security import (
    ALLOWED_ALGORITHMS,
    InvalidTokenError,
    verify_access_token,
)

TEST_KID = "test-key-1"


# ---------------------------------------------------------------------------------------------
# A local signing key and a stub JWKS cache
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def signing_key() -> ec.EllipticCurvePrivateKey:
    """An ES256 key pair, matching what local Supabase actually uses."""
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def jwks_document(signing_key: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    """The public half, in JWKS form — what Supabase publishes."""
    public_jwk = jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public_jwk.update({"kid": TEST_KID, "use": "sig", "alg": "ES256"})
    return {"keys": [public_jwk]}


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch: pytest.MonkeyPatch, jwks_document: dict[str, Any]) -> Iterator[None]:
    """Point the module-level JWKS cache at our local key, with no network access.

    Patching ``_fetch`` rather than the whole cache means the caching, staleness, and
    bounded-retry logic in ``JWKSCache`` is genuinely exercised — only the HTTP call is replaced.

    NOTE — ``security._jwks_cache`` is assigned directly rather than through ``monkeypatch``,
    which is deliberate and was the cause of a real intermittent failure.

    ``monkeypatch.setattr`` restores the value that was there *before* the patch. For the second
    and later tests in this module, that previous value is the stub cache left by the test before
    — so the module finished with a stub still installed on the global. The integration tests
    then reached for a real Supabase key inside a stub cache that had never heard of it, and
    failed with JWKSError instead of the expected result. It failed roughly one run in three,
    which is the worst kind of test failure to inherit.

    Assigning directly and always resetting in teardown means the global ends up as ``None``
    every time, whatever order the tests run in. ``tests/conftest.py`` resets it around every
    test as well, so this cannot recur even if a future fixture forgets.
    """
    cache = JWKSCache("http://test.invalid/jwks.json")

    def fake_fetch(self: JWKSCache) -> None:
        import time

        from jwt import PyJWK

        self._keys = {entry["kid"]: PyJWK.from_dict(entry) for entry in jwks_document["keys"]}
        self._fetched_at = time.monotonic()

    monkeypatch.setattr(JWKSCache, "_fetch", fake_fetch)
    security._jwks_cache = cache
    try:
        yield
    finally:
        security.reset_jwks_cache()


def make_token(
    signing_key: ec.EllipticCurvePrivateKey,
    *,
    subject: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    expires_in_seconds: int = 3600,
    algorithm: str = "ES256",
    kid: str | None = TEST_KID,
    omit: tuple[str, ...] = (),
    key: Any = None,
) -> str:
    """Mint a token, with every property individually overridable so each can be broken."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject or str(uuid.uuid4()),
        "iss": issuer if issuer is not None else settings.jwt_issuer,
        "aud": audience if audience is not None else settings.supabase_jwt_aud,
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        "iat": int(now.timestamp()),
        "email": "staff@example.com",
        "role": "authenticated",
    }
    for claim in omit:
        payload.pop(claim, None)

    headers = {"kid": kid} if kid else {}
    return jwt.encode(payload, key or signing_key, algorithm=algorithm, headers=headers)


# ---------------------------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------------------------


def test_a_valid_token_is_accepted(signing_key: ec.EllipticCurvePrivateKey) -> None:
    subject = uuid.uuid4()
    token = make_token(signing_key, subject=str(subject))

    claims = verify_access_token(token)

    assert claims.subject == subject
    assert claims.email == "staff@example.com"
    assert not claims.is_expired


def test_the_claims_carry_no_organization_or_role(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """SPEC §3.2: the tenant scope is never taken from the token.

    The token says who you are. Which practice you may see, and what you may do there, are read
    from our own database keyed on that identity. Asserting the absence structurally means a
    future "convenience" field cannot quietly become a trust boundary.
    """
    claims = verify_access_token(make_token(signing_key))

    assert not hasattr(claims, "organization_id")
    assert not hasattr(claims, "role")


# ---------------------------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------------------------


def test_a_token_signed_by_a_different_key_is_rejected() -> None:
    """The core guarantee. Everything else is detail."""
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = make_token(attacker_key)

    with pytest.raises(InvalidTokenError):
        verify_access_token(token)


def test_a_tampered_payload_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """Editing a claim invalidates the signature over it."""
    import base64
    import json

    token = make_token(signing_key)
    header, payload, signature = token.split(".")

    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["sub"] = str(uuid.uuid4())  # impersonate someone else
    repacked = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")

    with pytest.raises(InvalidTokenError):
        verify_access_token(f"{header}.{repacked}.{signature}")


# ---------------------------------------------------------------------------------------------
# Algorithm confusion — the attack the allowlist exists to stop
# ---------------------------------------------------------------------------------------------


def test_only_asymmetric_algorithms_are_accepted() -> None:
    """HS256 must never be in the allowlist.

    The attack: JWKS public keys are published to the world by design. If HS256 were accepted, an
    attacker could take that public key, use it as the HMAC *secret*, sign any payload they liked,
    and a verifier trusting the token's own ``alg`` header would check that HMAC with the same
    public key and accept it — minting a valid token for any user in the system.
    """
    assert "HS256" not in ALLOWED_ALGORITHMS
    assert "none" not in ALLOWED_ALGORITHMS
    assert set(ALLOWED_ALGORITHMS) <= {"ES256", "RS256", "ES384", "ES512", "RS384", "RS512"}


def test_an_unsigned_token_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """``alg: none`` is the oldest JWT attack there is, and still worth a test."""
    payload = {
        "sub": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.supabase_jwt_aud,
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    unsigned = jwt.encode(payload, key="", algorithm="none", headers={"kid": TEST_KID})

    with pytest.raises(InvalidTokenError):
        verify_access_token(unsigned)


# ---------------------------------------------------------------------------------------------
# Claim validation
# ---------------------------------------------------------------------------------------------


def test_an_expired_token_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    # Well past the 30-second clock-skew leeway.
    token = make_token(signing_key, expires_in_seconds=-3600)

    with pytest.raises(InvalidTokenError, match="expired"):
        verify_access_token(token)


def test_clock_skew_leeway_is_applied(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """A token that expired two seconds ago is still accepted.

    Machines disagree about the time by small amounts. Without leeway, a clock a few seconds fast
    would reject tokens that are genuinely valid, producing intermittent sign-outs that are
    miserable to diagnose.
    """
    token = make_token(signing_key, expires_in_seconds=-2)

    verify_access_token(token)  # must not raise


def test_a_token_from_another_issuer_is_rejected(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """A correctly signed token from a different Supabase project must not be accepted."""
    token = make_token(signing_key, issuer="https://someone-elses-project.supabase.co/auth/v1")

    with pytest.raises(InvalidTokenError, match="different authentication server"):
        verify_access_token(token)


def test_a_token_for_another_audience_is_rejected(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    token = make_token(signing_key, audience="some-other-service")

    with pytest.raises(InvalidTokenError, match="audience"):
        verify_access_token(token)


@pytest.mark.parametrize("claim", ["exp", "iss", "aud", "sub"])
def test_every_required_claim_is_required(
    signing_key: ec.EllipticCurvePrivateKey, claim: str
) -> None:
    """A token missing ``exp`` would be valid forever; one missing ``iss`` could come from
    anywhere. Each is individually required rather than assumed present."""
    token = make_token(signing_key, omit=(claim,))

    with pytest.raises(InvalidTokenError):
        verify_access_token(token)


def test_a_non_uuid_subject_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """``sub`` becomes a database lookup key, so its shape is checked before it is used."""
    token = make_token(signing_key, subject="not-a-uuid")

    with pytest.raises(InvalidTokenError, match="not a valid identifier"):
        verify_access_token(token)


# ---------------------------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   ", "not.a.token", "onlyonepart", "a.b", "a.b.c.d"])
def test_garbage_is_rejected_without_raising_something_unexpected(token: str) -> None:
    """Every malformed input must produce InvalidTokenError, never a stray ValueError.

    The dependency layer turns InvalidTokenError into a clean 401. Anything else escapes as an
    unhandled exception and becomes a 500 — which turns "you sent a bad token" into "the server
    is broken".
    """
    with pytest.raises(InvalidTokenError):
        verify_access_token(token)


def test_a_token_with_no_kid_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """Without a ``kid`` there is no way to choose a verification key."""
    token = make_token(signing_key, kid=None)

    with pytest.raises(InvalidTokenError, match="kid"):
        verify_access_token(token)


def test_an_unknown_kid_surfaces_as_a_jwks_error(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """A ``kid`` that is not published is a JWKSError, not an InvalidTokenError.

    The distinction drives the HTTP status: "we could not check" is a 503, while "we checked and
    refused" is a 401. Telling a user their session is invalid when the real problem is that our
    auth server is unreachable sends them to re-enter a password that was never wrong.
    """
    token = make_token(signing_key, kid="a-kid-that-was-never-published")

    with pytest.raises(JWKSError):
        verify_access_token(token)
