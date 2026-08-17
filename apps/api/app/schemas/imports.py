"""Schemas for the CSV import flow (SPEC §7)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RowProblemResponse(BaseModel):
    """One rejected row, as the preview screen displays it."""

    model_config = ConfigDict(from_attributes=True)

    row_number: int = Field(
        description=(
            "Line number in the uploaded file, counting the header as line 1 — so it matches "
            "what the practice sees when they open the file in a spreadsheet."
        )
    )
    category: str = Field(description="missing_required | invalid_email | invalid_date | duplicate")
    column: str
    value: str
    reason: str = Field(description="Plain-English explanation, safe to display.")


class ImportPreviewResponse(BaseModel):
    """What importing this file would do. Nothing has been written yet.

    The first four fields are the ones the demo reads aloud (SPEC §7.2)::

        327 records found          -> total_rows
        320 ready to import        -> valid_rows
          5 missing required info  -> missing_required
          2 invalid email addresses-> invalid_email
    """

    total_rows: int = Field(description="Records found in the file.")
    valid_rows: int = Field(description="Records ready to import.")

    new_count: int = Field(description="Of those, patients this practice does not yet have.")
    update_count: int = Field(description="And those that match an existing patient.")

    missing_required: int = Field(description="Rows missing required information.")
    invalid_email: int = Field(description="Rows with an invalid email address.")
    invalid_date: int = Field(description="Rows with an unusable last-visit date.")
    duplicate_in_file: int = Field(description="Rows repeating an earlier row in the same file.")

    problems: list[RowProblemResponse] = Field(
        default_factory=list,
        description=(
            "Every rejected row. Capped in the response for display; the downloadable error "
            "report contains all of them."
        ),
    )


class ImportResultResponse(BaseModel):
    """What the import actually did."""

    created: int = Field(description="New patients added.")
    updated: int = Field(description="Existing patients updated.")
    skipped: int = Field(description="Rows not imported because they had a problem.")
    total_rows: int = Field(description="Records found in the file.")
