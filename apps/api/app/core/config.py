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
#
# Guarded, because that walk only holds in a checkout. The container image copies apps/api to
# /app, so config.py sits at /app/app/core/config.py — four parents up is the filesystem root and
# five does not exist at all, which raised IndexError at import time and took the whole process
# down before it could log anything useful. There is no .env in a container regardless; the
# environment is the environment.
_CONFIG_PATH = Path(__file__).resolve()
REPO_ROOT = _CONFIG_PATH.parents[4] if len(_CONFIG_PATH.parents) > 4 else _CONFIG_PATH.parents[-1]

# One .env at the repo root serves both the API and (via a generated allowlist file) the web app.
# Keeping a single source avoids the classic bug where two env files drift and the frontend talks
# to a different Supabase project than the backend verifies tokens against.
#
# Absent in a deployed container, which is fine: pydantic-settings treats a missing env_file as
# "no file", and real environment variables take precedence over it in any case.
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

    # Additional browser origins allowed through CORS, comma-separated.
    #
    # This exists for Vercel, which gives every preview deployment its own hostname. `web_origin`
    # stays the single canonical origin; this is the escape hatch, and it is still an explicit
    # allowlist rather than a wildcard.
    extra_web_origins: str = ""

    # Whether this deployment is the public demo.
    #
    # The demo utilities (reset the data, run the reminder job) are switched off when APP_ENV is
    # production, because they are demo aids rather than product features. The public demo,
    # though, is a production deployment that genuinely wants them: the hourly reset is what stops
    # one visitor's clicking from degrading the demo for everyone after them.
    #
    # A separate opt-in flag rather than running the deployment as APP_ENV=development, because
    # the deployment really is production — it should have production logging, production error
    # handling, and production security headers. Lying about the environment to get one feature
    # would turn all of those off as a side effect.
    demo_mode: bool = False

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
    def allowed_origins(self) -> list[str]:
        """Every browser origin permitted by CORS, in order, without duplicates.

        Never a wildcard: with ``allow_credentials=True`` a wildcard is both rejected by browsers
        and, were it honoured, would let any site on the internet make authenticated requests on
        a signed-in user's behalf.
        """
        origins = [self.web_origin]
        origins.extend(
            candidate.rstrip("/")
            for raw in self.extra_web_origins.split(",")
            if (candidate := raw.strip())
        )
        # A wildcard is dropped rather than honoured. Browsers reject it alongside
        # allow_credentials anyway, so passing it through would not widen access — it would
        # silently break every cross-origin request instead, which is a far more confusing
        # failure than an origin that was quietly ignored.
        return [origin for origin in dict.fromkeys(origins) if origin != "*"]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def demo_utilities_enabled(self) -> bool:
        """Whether the admin-only demo tools (reset data, run reminder job) are exposed.

        These are demo aids, not product features, so they are switched off in production —
        unless DEMO_MODE is explicitly set, which is what the public demo deployment does. See
        the note on ``demo_mode`` above.
        """
        return self.app_env != "production" or self.demo_mode


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
