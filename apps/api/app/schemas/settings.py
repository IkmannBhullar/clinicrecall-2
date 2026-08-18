"""Clinic settings and reminder-rule shapes (SPEC §8)."""

from decimal import Decimal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ReminderRuleKey, UserRole


class ClinicSettingsResponse(BaseModel):
    """The practice's configuration.

    Note which of these are load-bearing rather than cosmetic: ``timezone`` decides what "today"
    means for every status in the product, and ``annual_interval_months`` decides when every
    patient is next due. The rest appear in the reminder email.
    """

    model_config = ConfigDict(from_attributes=True)

    clinic_name: str
    phone: str | None
    email: str | None
    website: str | None
    scheduling_url: str | None

    timezone: str = Field(description="IANA name. Decides what 'today' means for this practice.")
    annual_interval_months: int
    estimated_annual_visit_value: Decimal
    reminder_signature: str | None


class ClinicSettingsUpdate(BaseModel):
    """A settings change. Every field optional — the form sends only what changed."""

    clinic_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    scheduling_url: str | None = Field(default=None, max_length=500)

    timezone: str | None = None
    annual_interval_months: int | None = Field(default=None, ge=1, le=60)
    estimated_annual_visit_value: Decimal | None = Field(default=None, ge=0, le=100_000)
    reminder_signature: str | None = Field(default=None, max_length=1000)

    @field_validator("timezone")
    @classmethod
    def _must_be_a_real_timezone(cls, value: str | None) -> str | None:
        """Reject a name ZoneInfo cannot load.

        Accepting one would not fail here — it would fail quietly later, in `today_for_timezone`,
        which falls back to a default rather than raising. Every date in the product would then be
        computed against the wrong timezone with nothing on screen to say so.
        """
        if value is not None and value not in available_timezones():
            raise ValueError(
                f"'{value}' is not a recognised timezone. Use a name like 'America/Los_Angeles'."
            )
        return value


class ReminderRuleResponse(BaseModel):
    """One rule of the annual recall campaign."""

    model_config = ConfigDict(from_attributes=True)

    key: ReminderRuleKey
    days_relative_to_due_date: int
    enabled: bool


class ReminderRuleUpdate(BaseModel):
    """The only thing that can be changed about a rule (SPEC §8).

    "four rules with enable/disable toggles only — no automation builder". The offset is not
    editable, deliberately: a configurable schedule is a workflow builder in disguise, and SPEC §1
    puts those out of scope.
    """

    enabled: bool


class ReminderPerformance(BaseModel):
    """The delivery strip: Scheduled / Sent / Delivered / Failed."""

    scheduled: int
    sent: int
    delivered: int
    failed: int
    total: int


class AccountResponse(BaseModel):
    """Who you are signed in as, for the Settings page."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str
    last_name: str
    email: str
    role: UserRole


class SettingsPageResponse(BaseModel):
    """Everything the Settings screen renders, in one request."""

    clinic: ClinicSettingsResponse
    rules: list[ReminderRuleResponse]
    account: AccountResponse
    demo_utilities_enabled: bool = Field(
        description=(
            "Whether the demo tools (run the reminder job, reset demo data) are available. "
            "False in production — they are demo aids, not product features."
        )
    )
