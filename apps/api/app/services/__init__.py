"""Domain services.

All business rules live here. A service knows nothing about HTTP — no request objects, no
response models, no status codes — and nothing about SQL, which belongs to the repositories it
calls.

``RecallService`` is the centre of gravity. SPEC §5 requires that all status and date logic live
in one module, and that module is ``app/services/recall.py``. If you find yourself computing a
due date or deciding a status anywhere else, that is the bug.
"""

from app.services.recall import (
    COMPLETED_DISPLAY_WINDOW_DAYS,
    DUE_GRACE_DAYS,
    DUE_SOON_WINDOW_DAYS,
    PatientRecallInput,
    RecallService,
    compute_status,
    next_annual_due_date,
    recall_input_from_patient,
    today_for_timezone,
)

__all__ = [
    "COMPLETED_DISPLAY_WINDOW_DAYS",
    "DUE_GRACE_DAYS",
    "DUE_SOON_WINDOW_DAYS",
    "PatientRecallInput",
    "RecallService",
    "compute_status",
    "next_annual_due_date",
    "recall_input_from_patient",
    "today_for_timezone",
]
