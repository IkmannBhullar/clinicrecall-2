"""Work the API does once, when it boots.

Currently one job: bring every patient's cached status back in line with the calendar.

Why that is needed at all. ``patients.status`` is a denormalised copy of a pure function of
dates (SPEC §5.1). It goes stale without anyone touching the data — a patient who was DUE_SOON
yesterday is DUE today purely because a day passed. If the API is started in the morning after
sitting idle overnight, or restarted between demos, the first screen anyone sees would otherwise
show yesterday's statuses next to today's dates.

The reminder job performs the same sweep, so this is one of two safety nets rather than the only
one.
"""

from __future__ import annotations

import logging

from app.core.database import session_scope
from app.repositories.organizations import OrganizationRepository
from app.services.recall import RecallService

logger = logging.getLogger(__name__)


def recompute_all_patient_statuses() -> int:
    """Refresh cached statuses for every practice. Returns how many patients changed.

    Deliberately does not raise. A failure here — most likely the database not being up yet —
    must not prevent the API from starting: an application that refuses to boot because a
    background tidy-up failed is far harder to diagnose than one that logs a warning and serves
    slightly stale badges until the next sweep.
    """
    try:
        with session_scope() as session:
            organizations = OrganizationRepository(session).list_all()
            recall = RecallService(session)

            total_changed = sum(
                recall.recompute_organization(organization.id) for organization in organizations
            )

        if total_changed:
            logger.info(
                "Startup status recompute: %d patient(s) updated across %d organization(s)",
                total_changed,
                len(organizations),
            )
        return total_changed

    except Exception:
        logger.warning(
            "Startup status recompute failed — patient statuses may be stale until the "
            "reminder job next runs. The API is starting anyway.",
            exc_info=True,
        )
        return 0
