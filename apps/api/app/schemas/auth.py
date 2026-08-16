"""Schemas for the signed-in user.

Note what ``CurrentUserResponse`` does *not* contain: no token, no Supabase identifiers, and no
patient data. It is the answer to "who am I and what may I do", and nothing more.

``organization_id`` is included because the frontend uses it as a cache key. It is safe to expose
— the user's own tenant is hardly a secret to them — but it is worth being explicit that the API
never *accepts* it back. Sending it in a request has no effect; the scope is always re-derived
server-side from the token (SPEC §3.2).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole


class CurrentUserResponse(BaseModel):
    """The signed-in staff member."""

    # from_attributes lets this be built straight from the SQLAlchemy model.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    role: UserRole = Field(description="ADMIN or STAFF.")

    organization_id: uuid.UUID = Field(
        description=(
            "The practice this user belongs to. Informational only — the API always re-derives "
            "the tenant scope from the access token and ignores any organization sent by a "
            "client."
        )
    )


class OrganizationResponse(BaseModel):
    """The practice the signed-in user belongs to."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class SessionResponse(BaseModel):
    """Everything the web app needs on load: who you are, and where you work."""

    user: CurrentUserResponse
    organization: OrganizationResponse
