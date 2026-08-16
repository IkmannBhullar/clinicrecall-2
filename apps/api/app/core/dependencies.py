"""FastAPI dependencies — where a request acquires its identity and its tenant scope.

``get_current_user`` is the most security-critical function in the codebase. It performs the
resolution SPEC §3.2 mandates:

    Authorization header  →  verified JWT  →  JWT.sub
                          →  users.auth_user_id  →  users.organization_id

**The organization is never taken from the client.** Not from the body, not from a query
parameter, not from a header, and not from a claim inside the token. It is looked up in our own
database, keyed on the subject of a signature we verified against Supabase's public key. A caller
can therefore prove *who they are*, but cannot assert *which practice's data they may see*.

Every protected route depends on this one function, so there is exactly one place where that
resolution happens and exactly one place to audit.
"""

from __future__ import annotations

import logging

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import ForbiddenError, ServiceUnavailableError, UnauthorizedError
from app.core.jwks import JWKSError
from app.core.security import InvalidTokenError, TokenClaims, verify_access_token
from app.models.user import User
from app.repositories.users import UserRepository

logger = logging.getLogger(__name__)

# auto_error=False so a missing header reaches our handler rather than producing FastAPI's own
# 403 with a different body shape. Every failure then returns the standard error envelope.
bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")


def get_token_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenClaims:
    """Verify the bearer token and return its claims.

    Separated from ``get_current_user`` so that token verification can be tested — and reasoned
    about — without a database.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("No access token was supplied.")

    try:
        return verify_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        # Logged at INFO, not WARNING: an expired token is the single most common event in any
        # authenticated application and is not a sign of anything wrong.
        logger.info("Rejected access token: %s", exc)
        raise UnauthorizedError(str(exc)) from exc
    except JWKSError as exc:
        # We could not check, as opposed to checked and refused. Answering 401 here would tell
        # the user their credentials are bad when the truth is that our auth server is
        # unreachable — sending them to re-enter a password that was never the problem.
        logger.error("Could not verify token — JWKS unavailable: %s", exc)
        raise ServiceUnavailableError(
            "Could not verify your session because the authentication service is unreachable."
        ) from exc


def get_current_user(
    request: Request,
    claims: TokenClaims = Depends(get_token_claims),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the signed-in staff member, and with them the organization scope.

    A verified token whose subject has no matching row is rejected. That is a real situation
    rather than a theoretical one: a Supabase account can be created without a corresponding
    application user — by the Supabase dashboard, or by a half-finished invite flow — and such an
    account must not be able to reach any data at all.
    """
    user = UserRepository(db).get_by_auth_user_id(claims.subject)

    if user is None:
        logger.warning(
            "Valid token for Supabase subject %s has no application user; refusing access.",
            claims.subject,
        )
        raise UnauthorizedError("Your account is not set up for this application.")

    # Stashed on request.state so middleware and error handlers can reference the actor without
    # re-resolving. Only the identifiers — never the user object, and never their email.
    request.state.user_id = user.id
    request.state.organization_id = user.organization_id

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the ADMIN role.

    Guards the demo utilities (reset data, run the reminder job) and settings changes. There are
    only two roles by design — SPEC §1 rules out anything resembling a permissions builder, and a
    clinic has staff and it has an office manager.
    """
    if not user.is_admin:
        raise ForbiddenError("That action is restricted to administrators.")
    return user


def get_organization_id(user: User = Depends(get_current_user)) -> object:
    """The tenant scope for this request, for routes that need only that.

    Returned as the raw UUID so a route can pass it straight to a repository. Its provenance is
    the point: it came from our database via a verified token subject, never from the request.
    """
    return user.organization_id
