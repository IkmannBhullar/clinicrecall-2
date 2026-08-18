"""Creating the demo login through the Supabase admin API (SPEC §7.3).

    "Demo credentials created via the Supabase admin API in the seed script and documented in the
     README."

The seed cannot simply insert a row into ``auth.users``: GoTrue owns that table, hashes passwords
with its own parameters, and maintains identity rows alongside it. Writing to it directly produces
an account that exists but cannot sign in — and Alembic is forbidden from touching that schema
anyway (SPEC §3.1).

So the accounts are created the way any other client would create them, over HTTP, using the
service-role key. That key is server-side only and is used **nowhere else in the application**;
this module is the entire reason it is configured at all.
"""

from __future__ import annotations

import logging
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Short, because this runs during `make setup` and a hung request should fail rather than stall.
REQUEST_TIMEOUT_SECONDS = 15.0


class SupabaseAdminError(Exception):
    """The Supabase admin API could not be reached, or refused the request."""


def _admin_headers() -> dict[str, str]:
    if not settings.supabase_service_role_key:
        raise SupabaseAdminError(
            "SUPABASE_SERVICE_ROLE_KEY is not set, so demo accounts cannot be created. "
            "Run `make supabase-start`, which writes it into .env."
        )
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


def _find_user_by_email(client: httpx.Client, email: str) -> uuid.UUID | None:
    """Look up an existing account.

    This is what makes the seed idempotent with respect to auth: re-running must not fail because
    the account already exists, and must not create a second one.
    """
    response = client.get(
        f"{settings.supabase_url}/auth/v1/admin/users",
        headers=_admin_headers(),
        params={"page": 1, "per_page": 200},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    for user in response.json().get("users", []):
        if (user.get("email") or "").lower() == email.lower():
            return uuid.UUID(user["id"])

    return None


def ensure_auth_user(email: str, password: str) -> uuid.UUID:
    """Return the Supabase user id for this email, creating the account if needed.

    The password is reset on every run. That is deliberate: someone changes it while poking at a
    demo, and the next `make seed` should put the documented credentials back rather than leave
    the README lying.

    :raises SupabaseAdminError: if Supabase is unreachable or refuses
    """
    try:
        with httpx.Client() as client:
            existing = _find_user_by_email(client, email)

            if existing is not None:
                client.put(
                    f"{settings.supabase_url}/auth/v1/admin/users/{existing}",
                    headers=_admin_headers(),
                    json={"password": password, "email_confirm": True},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ).raise_for_status()
                logger.info("Reset the password for existing demo account %s", email)
                return existing

            created = client.post(
                f"{settings.supabase_url}/auth/v1/admin/users",
                headers=_admin_headers(),
                json={
                    "email": email,
                    "password": password,
                    # No confirmation email: there is no mail server in the local stack, and a
                    # demo account that cannot sign in until someone clicks a link nobody
                    # received would make `make setup` useless.
                    "email_confirm": True,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            created.raise_for_status()
            user_id = uuid.UUID(created.json()["id"])
            logger.info("Created demo account %s", email)
            return user_id

    except httpx.HTTPStatusError as exc:
        raise SupabaseAdminError(
            f"Supabase refused the request for {email}: "
            f"HTTP {exc.response.status_code} {exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise SupabaseAdminError(
            f"Could not reach Supabase at {settings.supabase_url}. "
            "Is the local stack running? Try `make supabase-start`."
        ) from exc


def supabase_is_reachable() -> bool:
    """Whether the auth service is up.

    Lets the seed carry on without accounts when it is not — useful in a test environment, and
    better than refusing to seed any patient data because one HTTP call failed.
    """
    try:
        response = httpx.get(f"{settings.supabase_url}/auth/v1/health", timeout=3.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500
