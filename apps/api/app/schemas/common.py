"""Shared response shapes.

These exist as Pydantic models rather than hand-built dicts so that they appear in the generated
OpenAPI document. The frontend reads that document, so a documented error shape is what lets the
client handle failures without guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """One field-level problem, used by validation failures."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Which field the problem concerns.")
    problem: str = Field(description="What is wrong with it, in plain language.")


class ErrorBody(BaseModel):
    """The contents of an error response."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(
        description=(
            "Stable machine-readable identifier, e.g. NOT_FOUND. Client logic should branch on "
            "this and never on the message, which is subject to rewording."
        ),
        examples=["NOT_FOUND"],
    )
    message: str = Field(
        description=(
            "Human-readable explanation, safe to display. Never contains a stack trace, a query, "
            "or an internal identifier."
        ),
        examples=["That record could not be found."],
    )
    correlation_id: str = Field(
        description=(
            "Identifies this request in the server logs. Quote it when reporting a problem — it "
            "is how the full technical detail is found without ever showing it to a user."
        ),
        examples=["3f2a91c4e8b7"],
    )
    details: list[ErrorDetail] | None = Field(
        default=None,
        description="Field-level problems, present only on validation failures.",
    )


class ErrorResponse(BaseModel):
    """The envelope every error in this API uses (SPEC §9)."""

    model_config = ConfigDict(frozen=True)

    error: ErrorBody
