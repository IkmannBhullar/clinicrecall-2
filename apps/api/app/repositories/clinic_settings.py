"""Clinic settings.

One row per organization, so this repository has no list operation — asking for "all settings for
this organization" can only ever return one thing.

``get_or_create`` matters more than it looks: ``RecallService`` reads ``timezone`` and
``annual_interval_months`` from here on every status computation, so a missing settings row would
not be a cosmetic gap — it would mean no patient's due date could be calculated at all.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.clinic_settings import ClinicSettings
from app.repositories.base import OrganizationScopedRepository


class ClinicSettingsRepository(OrganizationScopedRepository[ClinicSettings]):
    """Reads and writes the ``clinic_settings`` table."""

    model = ClinicSettings

    def get(self, organization_id: uuid.UUID) -> ClinicSettings | None:
        """Fetch the settings row for one practice, or None if it has not been created yet."""
        statement = self._scoped_select(organization_id)
        return self.session.execute(statement).scalar_one_or_none()

    def get_or_create(
        self,
        organization_id: uuid.UUID,
        *,
        clinic_name: str,
        timezone: str = "America/Los_Angeles",
        annual_interval_months: int = 12,
        estimated_annual_visit_value: Decimal | None = None,
    ) -> ClinicSettings:
        """Return the practice's settings, creating a default row if none exists.

        The defaults are only used on first creation. An existing row is returned untouched —
        re-running the seed must never reset a value someone deliberately changed during a demo.
        """
        existing = self.get(organization_id)
        if existing is not None:
            return existing

        settings = ClinicSettings(
            clinic_name=clinic_name,
            timezone=timezone,
            annual_interval_months=annual_interval_months,
            estimated_annual_visit_value=(
                estimated_annual_visit_value
                if estimated_annual_visit_value is not None
                else Decimal("250.00")
            ),
        )
        # `organization_id` is this table's primary key as well as its foreign key, so the base
        # class's assignment is what actually gives the row its identity.
        return self.add(organization_id, settings)
