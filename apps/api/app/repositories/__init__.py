"""Data access.

Every repository over a tenant-owned table takes ``organization_id`` as the first argument of
every method (SPEC §3.2). This is the application's primary defence against one practice seeing
another's patients — see ``app/repositories/base.py`` for how it is enforced and why it is done
this way rather than by remembering to add a filter.

There is exactly one method in this package that is not org-scoped:
``UserRepository.get_by_auth_user_id``. It is the bootstrap step that resolves a verified Supabase
identity into an organization, which is the value everything else is then scoped by.
"""

from app.repositories.activity_events import ActivityEventRepository
from app.repositories.base import OrganizationScopedRepository
from app.repositories.clinic_settings import ClinicSettingsRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.patients import PatientRepository
from app.repositories.reminder_events import ReminderEventRepository
from app.repositories.reminder_rules import ReminderRuleRepository
from app.repositories.users import UserRepository

__all__ = [
    "ActivityEventRepository",
    "ClinicSettingsRepository",
    "OrganizationRepository",
    "OrganizationScopedRepository",
    "PatientRepository",
    "ReminderEventRepository",
    "ReminderRuleRepository",
    "UserRepository",
]
