"""CSV import endpoints (SPEC §7).

Three routes, matching the flow SPEC §7.1 specifies — drag-and-drop, parse, validate, preview with
per-row errors, confirm, import:

* ``POST /patients/import/preview``  — what would happen. Writes nothing.
* ``POST /patients/import``          — do it. One transaction.
* ``POST /patients/import/errors``   — the same analysis as a downloadable error-report CSV.

**Why the file is uploaded again to commit, rather than the preview being cached.** Holding a
parsed file server-side between two requests means session state, an expiry policy, and a place
for one user's upload to be committed by another. Re-parsing is a few hundred milliseconds for a
realistic file and removes all three problems. It also means the commit validates the exact bytes
being imported, rather than trusting that a cached analysis still describes them.
"""

# NOTE: deliberately no `from __future__ import annotations` in this module.
#
# FastAPI resolves route parameter types at import time to build the request model. With
# postponed annotations every type is a string, and FastAPI cannot turn ForwardRef
# ('UploadFile') back into a class — so the file-upload routes below fail at import with
# "Invalid args for response field". Python 3.12 supports `X | Y` natively, so the future
# import buys nothing here anyway.

import logging

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.errors import ValidationFailedError
from app.core.rate_limit import IMPORT_LIMIT, limiter
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.imports import (
    ImportPreviewResponse,
    ImportResultResponse,
    RowProblemResponse,
)
from app.services.csv_import import CsvImportService, ImportPreview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients/import", tags=["import"])

#: How many individual row problems the JSON preview carries.
#:
#: A file with 5,000 bad rows would otherwise produce a response of several megabytes that no
#: interface can usefully display. The counts are always exact; this caps only the detail list,
#: and the error-report download has every one of them.
MAX_PROBLEMS_IN_RESPONSE = 100


def _require_csv(upload: UploadFile) -> None:
    """Reject anything that is obviously not a CSV, before parsing it.

    Checks the filename rather than the content type: browsers report CSV files as
    ``application/vnd.ms-excel``, ``text/plain``, or ``application/octet-stream`` depending on
    the operating system and what is installed, so the content type is not a reliable signal.
    """
    name = (upload.filename or "").lower()

    if not name.endswith(".csv"):
        raise ValidationFailedError(
            "Please upload a .csv file. If your patient list is a spreadsheet, use "
            "File → Save As and choose CSV."
        )


def _to_response(preview: ImportPreview) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        total_rows=preview.total_rows,
        valid_rows=preview.valid_rows,
        new_count=preview.new_count,
        update_count=preview.update_count,
        missing_required=preview.missing_required,
        invalid_email=preview.invalid_email,
        invalid_date=preview.invalid_date,
        duplicate_in_file=preview.duplicate_in_file,
        problems=[
            RowProblemResponse.model_validate(problem)
            for problem in preview.problems[:MAX_PROBLEMS_IN_RESPONSE]
        ],
    )


@router.post(
    "/preview",
    response_model=ImportPreviewResponse,
    summary="Preview a patient CSV without importing it",
    responses={
        422: {"model": ErrorResponse, "description": "The file could not be read"},
        429: {"model": ErrorResponse, "description": "Too many import attempts"},
    },
)
@limiter.limit(IMPORT_LIMIT)
def preview_import(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    file: UploadFile = File(description="Patient CSV export"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportPreviewResponse:
    """Analyse the file and report what importing it would do.

    Writes nothing. A practice handing over their patient list should see exactly what is about
    to happen to it before anything happens (SPEC §7.1).
    """
    _require_csv(file)

    preview = CsvImportService(db).build_preview(user.organization_id, file.file)  # type: ignore[arg-type]

    logger.info(
        "Import preview for organization %s: %d rows, %d valid, %d problems",
        user.organization_id,
        preview.total_rows,
        preview.valid_rows,
        len(preview.problems),
    )
    return _to_response(preview)


@router.post(
    "",
    response_model=ImportResultResponse,
    summary="Import a patient CSV",
    responses={
        422: {"model": ErrorResponse, "description": "The file could not be read"},
        429: {"model": ErrorResponse, "description": "Too many import attempts"},
    },
)
@limiter.limit(IMPORT_LIMIT)
def run_import(
    request: Request,
    response: Response,
    file: UploadFile = File(description="Patient CSV export"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResultResponse:
    """Import the valid rows, in a single transaction.

    Rows with problems are skipped and reported — never silently dropped (SPEC §7.1). If anything
    raises, the whole import rolls back, so a failure cannot leave a practice with half their
    patients loaded and no way to tell which half.
    """
    _require_csv(file)

    service = CsvImportService(db)
    preview = service.build_preview(user.organization_id, file.file)  # type: ignore[arg-type]
    result = service.commit(user.organization_id, preview, actor_user_id=user.id)

    # The one commit for the whole file. Everything above ran inside this transaction.
    db.commit()

    logger.info(
        "Import for organization %s by user %s: %d created, %d updated, %d skipped",
        user.organization_id,
        user.id,
        result.created,
        result.updated,
        result.skipped,
    )

    return ImportResultResponse(
        created=result.created,
        updated=result.updated,
        skipped=result.skipped,
        total_rows=preview.total_rows,
    )


@router.post(
    "/errors",
    response_class=PlainTextResponse,
    summary="Download the error report for a patient CSV",
    responses={422: {"model": ErrorResponse, "description": "The file could not be read"}},
)
@limiter.limit(IMPORT_LIMIT)
def download_error_report(
    request: Request,
    response: Response,
    file: UploadFile = File(description="Patient CSV export"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """Return the rejected rows as a CSV the practice can open and work through (SPEC §7.1).

    Contains **every** problem, not the capped list the JSON preview carries — this is the file
    someone sits with next to their export, and a truncated one would send them back for another
    round.
    """
    _require_csv(file)

    service = CsvImportService(db)
    preview = service.build_preview(user.organization_id, file.file)  # type: ignore[arg-type]

    return PlainTextResponse(
        content=service.build_error_report(preview),
        media_type="text/csv",
        headers={
            # Prompts a download rather than rendering in the browser.
            "Content-Disposition": 'attachment; filename="import-errors.csv"',
        },
    )
