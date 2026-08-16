"""Access token verification.

Turns the bearer token on a request into a set of trusted claims, or refuses it. Everything
downstream — most importantly the organization a request is scoped to — rests on this function
being right.

Three properties are non-negotiable (SPEC §3.2):

1. **The signature is checked against a published public key**, fetched by ``kid`` from Supabase's
   JWKS. We never hold a secret capable of *minting* a token, only of checking one.
2. **The algorithm is allowlisted.** See the note below; this is not a formality.
3. **``iss``, ``aud`` and ``exp`` are all validated.** A correctly signed token issued by a
   different project, or for a different audience, or expired an hour ago, is refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from app.core.config import settings
from app.core.jwks import JWKSCache, JWKSError

# ---------------------------------------------------------------------------------------------
# Accepted algorithms
# ---------------------------------------------------------------------------------------------
#
# ASYMMETRIC ONLY. This allowlist is a security control, not configuration.
#
# The attack it prevents is algorithm confusion. If HS256 were accepted, an attacker could take
# the JWKS public key — which is, by design, published to the world — and use it as the HMAC
# *secret* to sign a token of their choosing. A verifier that trusts the token's own `alg` header
# would then check that HMAC with the same public key and accept it. The attacker would be able
# to mint a valid token for any user in the system.
#
# With an asymmetric-only allowlist, forging a token requires the private key, which never leaves
# Supabase. Local Supabase issues ES256; hosted projects may use RS256; both are listed.
#
# "none" is not listed, and must never be.
ALLOWED_ALGORITHMS = ["ES256", "RS256"]

# Tolerance for clock skew between this server and the auth server. Thirty seconds is generous
# for machines on the same host and small enough that an expired token stays expired.
CLOCK_SKEW_LEEWAY_SECONDS = 30


class InvalidTokenError(Exception):
    """The token was present but cannot be trusted.

    Distinct from :class:`~app.core.jwks.JWKSError`, which means we were unable to check. This
    one means we checked and the answer was no — a client error, not a server error.
    """


@dataclass(frozen=True)
class TokenClaims:
    """The claims we trust after verification.

    Deliberately narrow. Supabase puts a good deal more in a token, but this application needs
    exactly one thing from it — ``sub``, the Supabase user id — because that is the hinge the
    tenancy model turns on:

        JWT.sub  →  users.auth_user_id  →  users.organization_id

    Note what is *absent*: there is no organization here, and no role. Both are read from our own
    database, keyed on ``subject``. A token cannot assert which practice it belongs to or what it
    is allowed to do (SPEC §3.2).
    """

    subject: uuid.UUID
    email: str | None
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        """Belt and braces — PyJWT already rejects expired tokens during decode."""
        return self.expires_at <= datetime.now(UTC)


# One cache for the process. Built lazily so importing this module does not make a network call,
# which would make every test that touches it slow and fragile.
_jwks_cache: JWKSCache | None = None


def get_jwks_cache() -> JWKSCache:
    """Return the process-wide JWKS cache, creating it on first use."""
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = JWKSCache(settings.jwks_url)
    return _jwks_cache


def reset_jwks_cache() -> None:
    """Discard the cache. Used by tests, and after a configuration change."""
    global _jwks_cache
    _jwks_cache = None


def verify_access_token(token: str) -> TokenClaims:
    """Verify a Supabase access token and return its trusted claims.

    :raises InvalidTokenError: the token is malformed, unsigned, expired, or not ours
    :raises JWKSError: the signing keys could not be obtained (a server-side failure)
    """
    if not token or not token.strip():
        raise InvalidTokenError("No token supplied.")

    # Read the header *without* verifying, purely to learn which key to verify with. Nothing from
    # this is trusted: `alg` in particular is ignored in favour of our own allowlist below.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(f"Malformed token: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise InvalidTokenError("Token header has no `kid`, so no signing key can be selected.")

    try:
        signing_key = get_jwks_cache().get_key(kid)
    except JWKSError:
        # Propagated unchanged. This is "we could not check", not "the token is bad", and the two
        # must produce different HTTP responses — 503 rather than 401.
        raise

    try:
        payload = jwt.decode(
            token,
            key=signing_key.key,
            # Our allowlist, never the token's own `alg` header. See the note at the top.
            algorithms=ALLOWED_ALGORITHMS,
            audience=settings.supabase_jwt_aud,
            issuer=settings.jwt_issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={
                # Every one of these must be present. A token missing `exp` would otherwise be
                # valid forever, and one missing `iss` could come from any project.
                "require": ["exp", "iss", "aud", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired. Sign in again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidTokenError("Token audience is not accepted by this API.") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidTokenError("Token was issued by a different authentication server.") from exc
    except jwt.PyJWTError as exc:
        # Covers a bad signature, a disallowed algorithm, and a missing required claim. The
        # message stays generic on purpose — telling a caller precisely which check failed helps
        # them iterate towards a forgery.
        raise InvalidTokenError(f"Token could not be verified: {exc}") from exc

    subject_raw = payload.get("sub")
    try:
        subject = uuid.UUID(str(subject_raw))
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError(
            f"Token subject {subject_raw!r} is not a valid identifier."
        ) from exc

    return TokenClaims(
        subject=subject,
        email=payload.get("email"),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )
