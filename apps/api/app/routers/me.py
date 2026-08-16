"""The signed-in user's own session.

The web app calls ``GET /me`` once on load to find out who is signed in, what they are allowed to
do, and which practice they work for.

It is also the smallest possible proof that the whole authentication chain works end to end: a
Supabase token verified against a JWKS public key, resolved to an application user, resolved to
an organization. If this endpoint returns the right answer, tenancy is wired correctly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.errors import NotFoundError
from app.core.rate_limit import READ_LIMIT, limiter
from app.models.user import User
from app.repositories.organizations import OrganizationRepository
from app.schemas.auth import CurrentUserResponse, OrganizationResponse, SessionResponse
from app.schemas.common import ErrorResponse

router = APIRouter(tags=["session"])


@router.get(
    "/me",
    response_model=SessionResponse,
    summary="Who am I?",
    responses={
        401: {"model": ErrorResponse, "description": "Missing, expired, or invalid token"},
        503: {"model": ErrorResponse, "description": "Authentication service unreachable"},
    },
)
@limiter.limit(READ_LIMIT)
def read_current_session(
    # Both of these are unused in the body but required by @limiter.limit — see the note on
    # RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py. `request` identifies the caller;
    # `response` is where slowapi writes the X-RateLimit-* headers.
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Return the signed-in user and their practice.

    The organization is fetched by the id resolved from the token, never by one supplied in the
    request — which is the whole point of the dependency chain behind ``get_current_user``.
    """
    organization = OrganizationRepository(db).get_by_id(user.organization_id)

    if organization is None:
        # A user row pointing at a missing organization means the database is inconsistent. It is
        # not something a client can cause, and it should never happen — but returning a
        # confusing partial response would be worse than saying so plainly.
        raise NotFoundError("Your practice could not be found.")

    return SessionResponse(
        user=CurrentUserResponse.model_validate(user),
        organization=OrganizationResponse.model_validate(organization),
    )
