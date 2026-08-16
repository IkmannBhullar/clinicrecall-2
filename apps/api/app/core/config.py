"""Application settings.

Every environment variable the API reads is declared here, in one place, with a type. Nothing
else in the codebase calls ``os.environ`` — if you need a new setting, add a field here.

Why a single typed Settings object rather than scattered ``os.getenv`` calls:

* A missing or malformed variable fails loudly at startup with a readable message, instead of
  producing ``None`` that surfaces as a confusing error twenty minutes into a demo.
* ``mypy`` can see the types, so ``settings.annual_reminder_catch_up_days + 1`` is checked.
* There is exactly one document to read when you want to know what the service is configured by.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/core/config.py -> core -> app -> api -> apps -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[4]

# One .env at the repo root serves both the API and (via a generated allowlist file) the web app.
# Keeping a single source avoids the classic bug where two env files drift and the frontend talks
# to a different Supabase project than the backend verifies tokens against.
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # Real environment variables win over the .env file, which is what CI and containers need.
        extra="ignore",
        case_sensitive=False,
    )

    # -----------------------------------------------------------------------------------------
    # Application environment
    # -----------------------------------------------------------------------------------------

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # -----------------------------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------------------------

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"

    # -----------------------------------------------------------------------------------------
    # Supabase auth
    # -----------------------------------------------------------------------------------------

    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""

    # Server-side only. Used solely by the seed script to create demo user accounts through the
    # Supabase admin API. It is never sent to a browser and never used to serve a request.
    supabase_service_role_key: str = ""

    supabase_jwt_aud: str = "authenticated"

    # Left blank in .env and derived below, so there is one fewer URL to keep in sync by hand.
    supabase_jwks_url: str = ""

    # -----------------------------------------------------------------------------------------
    # Service configuration
    # -----------------------------------------------------------------------------------------

    web_origin: str = "http://localhost:3000"
    api_base_url: str = "http://127.0.0.1:8000"

    # Shared secret protecting POST /internal/jobs/process-reminders. Compared in constant time.
    job_token: str = ""

    # Signs one-click unsubscribe links so they cannot be forged or guessed.
    unsubscribe_token_secret: str = ""

    # -----------------------------------------------------------------------------------------
    # Email
    # -----------------------------------------------------------------------------------------

    email_provider: Literal["mock", "resend", "ses"] = "mock"
    email_provider_api_key: str = ""
    email_from_address: str = "reminders@example.com"
    email_from_name: str = "ClinicRecall"

    # -----------------------------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------------------------

    @field_validator("supabase_url", "web_origin", "api_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        """Normalise URLs so string comparison (notably CORS origin matching) is reliable.

        ``http://localhost:3000`` and ``http://localhost:3000/`` are the same place to a human
        and different strings to a CORS check, which produces a bug that looks like a mystery.
        """
        return value.rstrip("/")

    # -----------------------------------------------------------------------------------------
    # Derived values
    # -----------------------------------------------------------------------------------------

    @property
    def jwks_url(self) -> str:
        """Where to fetch Supabase's public token-signing keys.

        Uses the explicit override if one is set, otherwise derives the standard Supabase path.
        """
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        """The ``iss`` claim Supabase stamps on tokens it issues."""
        return f"{self.supabase_url}/auth/v1"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def demo_utilities_enabled(self) -> bool:
        """Whether the admin-only demo tools (reset data, run reminder job) are exposed.

        These are demo aids, not product features, so they are switched off in production.
        """
        return self.app_env != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once.

    Cached because reading and validating the .env file on every request would be wasteful, and
    because settings changing mid-process would make behaviour hard to reason about.

    Tests that need different settings call ``get_settings.cache_clear()``.
    """
    return Settings()


# Convenient module-level handle for the common case.
settings = get_settings()
