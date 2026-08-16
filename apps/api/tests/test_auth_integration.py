"""End-to-end authentication against the real local Supabase stack.

Every other auth test in this suite mints its own tokens. That is the right way to test the
rejection cases — a real auth server will never hand you a token signed with the wrong key — but
it leaves one thing unproven: that our verification actually accepts what Supabase actually
issues.

The gap is not hypothetical. Verification could be checking the wrong issuer format, expecting a
different audience, or supporting a different signing algorithm than the one in use, and every
hermetic test would still pass while nobody could sign in.

So this file does the real thing: creates a user through the Supabase admin API, signs in as
them, and puts the resulting token through the same verification path a browser request takes.

These tests skip cleanly if Supabase is unreachable, so the suite still runs on a machine with no
Docker.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import InvalidTokenError, reset_jwks_cache, verify_access_token
from app.main import app
from app.models.enums import UserRole
from app.models.organization import Organization
from app.models.user import User

pytestmark = pytest.mark.integration

TEST_PASSWORD = "integration-test-password-8f3a"


def supabase_available() -> bool:
    try:
        response = httpx.get(f"{settings.supabase_url}/auth/v1/health", timeout=2.0)
    except Exception:
        return False
    return response.status_code < 500


requires_supabase = pytest.mark.skipif(
    not supabase_available(),
    reason="Local Supabase stack is not running (start it with `make supabase-start`).",
)


@pytest.fixture(scope="module")
def supabase_user() -> Iterator[tuple[uuid.UUID, str]]:
    """Create a throwaway Supabase account, and remove it afterwards.

    Yields ``(auth_user_id, access_token)`` — a genuine ES256 token signed by the local GoTrue
    instance with a key we have never seen in Python.

    **Module-scoped on purpose.** GoTrue rate-limits sign-in to 30 requests per 5 minutes per IP
    (``sign_in_sign_ups`` in ``supabase/config.toml``). A function-scoped fixture would sign in
    once per test, so running ``make verify`` about ten times in five minutes — which is an
    entirely normal afternoon — would start failing the suite with an error that looks nothing
    like a rate limit. One sign-in per module makes that three times less likely, and the
    handling below makes it honest when it happens anyway.
    """
    email = f"integration-{uuid.uuid4().hex[:10]}@example.com"
    admin_headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }

    created = httpx.post(
        f"{settings.supabase_url}/auth/v1/admin/users",
        headers=admin_headers,
        json={"email": email, "password": TEST_PASSWORD, "email_confirm": True},
        timeout=10.0,
    )
    if created.status_code == 429:
        pytest.skip("Supabase auth rate limit reached; skipping the live-token tests.")
    created.raise_for_status()
    auth_user_id = uuid.UUID(created.json()["id"])

    try:
        signed_in = httpx.post(
            f"{settings.supabase_url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": settings.supabase_anon_key},
            json={"email": email, "password": TEST_PASSWORD},
            timeout=10.0,
        )

        if signed_in.status_code == 429:
            # Skipping is the honest outcome: the test could not run, and reporting a failure
            # would send someone hunting for an authentication bug that does not exist.
            pytest.skip("Supabase auth rate limit reached; skipping the live-token tests.")
        signed_in.raise_for_status()

        yield auth_user_id, signed_in.json()["access_token"]
    finally:
        # Always clean up, even on failure — otherwise a flaky run slowly fills the local auth
        # database with orphaned accounts.
        httpx.delete(
            f"{settings.supabase_url}/auth/v1/admin/users/{auth_user_id}",
            headers=admin_headers,
            timeout=10.0,
        )
        reset_jwks_cache()


# ---------------------------------------------------------------------------------------------
# Verification against real keys
# ---------------------------------------------------------------------------------------------


@requires_supabase
def test_a_real_supabase_token_verifies(supabase_user: tuple[uuid.UUID, str]) -> None:
    """The whole point of this file.

    A token minted by GoTrue, verified against a public key fetched over HTTP from the live JWKS
    endpoint. If the issuer format, the audience, or the algorithm allowlist were wrong, this is
    the only test in the suite that would notice.
    """
    auth_user_id, token = supabase_user

    claims = verify_access_token(token)

    assert claims.subject == auth_user_id
    assert not claims.is_expired


@requires_supabase
def test_a_real_token_with_a_tampered_signature_is_rejected(
    supabase_user: tuple[uuid.UUID, str],
) -> None:
    """Real key, real token, one flipped *byte* of signature.

    The tampering is done by decoding to bytes, flipping a bit, and re-encoding — rather than by
    editing a character of the base64 text, which is the obvious approach and is wrong.

    An ES256 signature is 64 raw bytes, encoded as 86 base64url characters. 86 x 6 = 516 bits
    carrying 512 bits of payload, so the final character's low bits are padding and carry no
    information. Changing that character can therefore decode to byte-for-byte the same
    signature, leaving the token genuinely, correctly valid.

    An earlier version of this test did exactly that and failed roughly one run in three with
    "DID NOT RAISE" — which reads like a signature-verification hole and was in fact a flaw in
    the test. Flipping a byte is unambiguous.
    """
    import base64

    _, token = supabase_user
    header, payload, signature = token.split(".")

    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    tampered_raw = bytes([raw[0] ^ 0xFF]) + raw[1:]
    tampered = base64.urlsafe_b64encode(tampered_raw).decode().rstrip("=")

    assert tampered != signature, "the tampering was a no-op"

    with pytest.raises(InvalidTokenError):
        verify_access_token(f"{header}.{payload}.{tampered}")


@requires_supabase
def test_the_jwks_endpoint_publishes_an_asymmetric_key() -> None:
    """Guards an assumption the algorithm allowlist depends on.

    If a future Supabase release switched local development back to a shared HMAC secret, the
    JWKS document would change shape and every sign-in would fail with a confusing error. Better
    to fail here, with a message naming the cause.
    """
    document = httpx.get(
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", timeout=5.0
    ).json()

    assert document["keys"], "Supabase published an empty JWKS document"
    for key in document["keys"]:
        assert key["alg"] in {"ES256", "RS256"}, (
            f"Supabase is signing with {key['alg']}, which is not in our allowlist. "
            "Symmetric algorithms must not be accepted — see app/core/security.py."
        )
        assert key.get("kid"), "A JWKS key with no `kid` cannot be selected by a token header"


# ---------------------------------------------------------------------------------------------
# Through the HTTP layer
# ---------------------------------------------------------------------------------------------


@requires_supabase
def test_a_real_token_authenticates_a_real_request(
    db: Session, organization: Organization, supabase_user: tuple[uuid.UUID, str]
) -> None:
    """The complete chain, with nothing stubbed:

    Supabase sign-in  ->  Authorization header  ->  JWKS fetch  ->  signature check
                      ->  JWT.sub  ->  users.auth_user_id  ->  users.organization_id
    """
    auth_user_id, token = supabase_user

    db.add(
        User(
            organization_id=organization.id,
            auth_user_id=auth_user_id,
            first_name="Alex",
            last_name="Morgan",
            email=f"alex+{uuid.uuid4().hex[:6]}@example.com",
            role=UserRole.ADMIN,
        )
    )
    db.flush()

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["organization"]["id"] == str(organization.id)


@requires_supabase
def test_a_real_token_for_an_unknown_user_is_refused(
    db: Session, supabase_user: tuple[uuid.UUID, str]
) -> None:
    """A perfectly valid Supabase account with no application user reaches nothing.

    This is the realistic version of the case: the account genuinely exists in GoTrue and the
    token genuinely verifies. Only the application-side row is missing.
    """
    _, token = supabase_user

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
