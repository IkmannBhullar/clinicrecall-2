"""The dashboard endpoint (SPEC §8).

One request returns everything the opening screen needs: the KPI cards, the recall overview, the
patients needing attention, and the recent reminder activity.

One request rather than five, because the dashboard is the first thing anyone sees and five
round trips means five chances to render half a screen. It also means the numbers are all
computed against the same moment — a KPI card and a table disagreeing because they were fetched
two seconds apart is exactly the sort of thing an office manager notices and stops trusting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import READ_LIMIT, limiter
from app.models.enums import PatientStatus
from app.models.user import User
from app.repositories.reminder_events import ReminderEventRepository
from app.schemas.patients import PatientSummary
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class StatusCount(BaseModel):
    """One bar of the Recall Overview."""

    status: PatientStatus
    count: int


class RecentReminder(BaseModel):
    """One line of the recent reminder activity feed."""

    patient_public_id: str
    patient_initials: str = Field(
        description="Initials rather than a name, matching the activity feed's convention."
    )
    status: str
    sent_at: datetime | None
    rule_key: str | None


class RevenueSummary(BaseModel):
    """Estimated Revenue Recovered, with the definition it is computed from.

    ``definition`` travels with the number on purpose. SPEC §8 requires the formula to be shown
    on hover, and shipping it alongside the value means the interface cannot display a stale
    description of a calculation that has since changed.
    """

    appointments_recovered: int
    estimated_value: Decimal
    value_per_visit: Decimal
    definition: str


class DashboardResponse(BaseModel):
    """Everything the dashboard renders."""

    # KPI cards, in the order SPEC §8 lists them.
    total_patients: int
    due_this_month: int
    overdue: int
    reminders_sent_this_month: int

    revenue: RevenueSummary
    recall_overview: list[StatusCount]
    needs_attention: list[PatientSummary]
    recent_reminders: list[RecentReminder]

    today: date = Field(
        description=(
            "The practice's own current date, in its configured timezone. Sent so the interface "
            "renders 'due in 11 days' from the same 'today' the statuses were computed against "
            "— a browser in another timezone would otherwise disagree with the badges."
        )
    )


@router.get("", response_model=DashboardResponse, summary="Dashboard metrics")
@limiter.limit(READ_LIMIT)
def get_dashboard(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DashboardResponse:
    """Build the opening screen."""
    service = DashboardService(db)
    events = ReminderEventRepository(db)

    metrics = service.build(user.organization_id)
    attention = service.list_needing_attention(user.organization_id)

    # Imported here rather than at module scope to avoid a circular import: the patients router
    # imports schemas that this module also uses.
    from app.routers.patients import _summary

    recent = [
        RecentReminder(
            patient_public_id=event.patient.public_id,
            patient_initials=event.patient.initials,
            status=event.status.value,
            sent_at=event.sent_at,
            rule_key=event.reminder_rule.key.value if event.reminder_rule else None,
        )
        for event in events.list_recent(user.organization_id, limit=6)
    ]

    return DashboardResponse(
        total_patients=metrics.total_patients,
        due_this_month=metrics.due_this_month,
        overdue=metrics.overdue,
        reminders_sent_this_month=metrics.reminders_sent_this_month,
        revenue=RevenueSummary(
            appointments_recovered=metrics.appointments_recovered,
            estimated_value=metrics.estimated_revenue_recovered,
            value_per_visit=metrics.estimated_visit_value,
            definition=metrics.revenue_definition,
        ),
        # Ordered most-urgent first, so the Recall Overview reads left to right as a progression
        # rather than as an arbitrary list.
        recall_overview=[
            StatusCount(status=status, count=metrics.status_counts.get(status, 0))
            for status in (
                PatientStatus.OVERDUE,
                PatientStatus.DUE,
                PatientStatus.DUE_SOON,
                PatientStatus.SCHEDULED,
                PatientStatus.ACTIVE,
                PatientStatus.COMPLETED,
                PatientStatus.INACTIVE,
            )
        ],
        needs_attention=[_summary(patient, events) for patient in attention],
        recent_reminders=recent,
        today=service.recall.today_for_org(user.organization_id),
    )
