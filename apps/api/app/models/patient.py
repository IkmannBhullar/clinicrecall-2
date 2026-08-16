"""Patients — the central table.

A patient row holds only what annual recall needs: who they are, how to reach them, when they
were last seen, and when they are next due. There are no clinical fields here and there must
never be any (SPEC §1). If a column would tell you *why* someone is visiting, it does not belong.

Two columns are derived rather than authored, and only one place in the codebase may write them:

* ``next_annual_due_date`` — ``last_annual_visit_date + annual_interval_months``
* ``status``               — the pure function ``RecallService.compute_status``

Both are cached on the row so that listing and filtering thousands of patients is a plain indexed
query rather than a computation per row. The cost of caching is drift, which is why SPEC §5.1
requires a test that recomputes every patient and asserts the stored value matches.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import PUBLIC_ID_LENGTH, generate_public_id
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PatientStatus, PreferredContactMethod

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.reminder_event import ReminderEvent


class Patient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person the practice should see once a year."""

    __tablename__ = "patients"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The only identifier that ever appears in a URL or an API response (SPEC §4.2).
    # Generated in Python at insert so the value exists before the row is flushed.
    public_id: Mapped[str] = mapped_column(
        String(PUBLIC_ID_LENGTH),
        nullable=False,
        unique=True,
        index=True,
        default=generate_public_id,
    )

    # The practice's own identifier for this patient, carried over from their existing system.
    # Optional, because plenty of CSV exports do not include one — but when it is present it is
    # the most reliable key for deciding whether an import row is a new patient or an update
    # (SPEC §7.1).
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ---------------------------------------------------------------------------------------
    # Identity and contact
    # ---------------------------------------------------------------------------------------

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Required: a patient with no email cannot be reminded, and reminding is the entire product.
    # Stored as entered but always compared case-insensitively (see the unique index below).
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    preferred_contact_method: Mapped[PreferredContactMethod] = mapped_column(
        SAEnum(
            PreferredContactMethod,
            name="preferred_contact_method",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PreferredContactMethod.EMAIL,
    )

    # ---------------------------------------------------------------------------------------
    # Recall dates
    #
    # All three are DATE, never a timestamp. "Due on the 14th" is a calendar fact about a clinic's
    # day, not an instant in time, and storing it as a timestamp immediately raises the question
    # of which timezone midnight belongs to (SPEC §5.3).
    # ---------------------------------------------------------------------------------------

    last_annual_visit_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Derived from the line above plus clinic_settings.annual_interval_months.
    next_annual_due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Set when staff mark an appointment as booked. Nullable because most patients do not have
    # one. Note this expires: once the date passes without a completion, the patient falls back
    # to their date-derived status rather than showing "Scheduled" forever (SPEC §5.2).
    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---------------------------------------------------------------------------------------
    # Derived status cache
    # ---------------------------------------------------------------------------------------

    status: Mapped[PatientStatus] = mapped_column(
        SAEnum(
            PatientStatus,
            name="patient_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PatientStatus.ACTIVE,
    )

    # ---------------------------------------------------------------------------------------
    # Consent
    # ---------------------------------------------------------------------------------------

    # Staff-controlled pause. Distinct from opting out: the practice chose to stop, the patient
    # did not ask to be left alone.
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # Patient-controlled, set by the one-click unsubscribe link in every reminder (SPEC §6.5).
    # A timestamp rather than a boolean because *when* someone withdrew consent is the part that
    # matters if it is ever questioned.
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---------------------------------------------------------------------------------------
    # Relationships
    # ---------------------------------------------------------------------------------------

    organization: Mapped[Organization] = relationship(back_populates="patients")

    reminder_events: Mapped[list[ReminderEvent]] = relationship(
        back_populates="patient",
        cascade="all, delete-orphan",
        order_by="ReminderEvent.created_at.desc()",
    )

    # ---------------------------------------------------------------------------------------
    # Indexes
    # ---------------------------------------------------------------------------------------

    __table_args__ = (
        # Dedupe key 1 (SPEC §7.1). A partial index, because plenty of patients have no
        # external_id and Postgres would otherwise treat every NULL as distinct anyway — the
        # WHERE clause makes that intent explicit and keeps the index small.
        Index(
            "uq_patients_organization_id_external_id",
            "organization_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        # Dedupe key 2 (SPEC §4.1). Indexed on lower(email) so that "J.Smith@Example.com" and
        # "j.smith@example.com" are the same patient. Doing this in the index rather than by
        # normalising on write means the guarantee holds even for rows written by a migration or
        # by hand.
        Index(
            "uq_patients_organization_id_lower_email",
            "organization_id",
            func.lower(text("email")),
            unique=True,
        ),
        # Serves "who is due this month?" — the query behind the dashboard and the reminder job.
        Index(
            "ix_patients_organization_id_next_annual_due_date",
            "organization_id",
            "next_annual_due_date",
        ),
        # Serves the status filter chips on the patients list.
        Index("ix_patients_organization_id_status", "organization_id", "status"),
    )

    def __init__(self, **kwargs: Any) -> None:
        """Construct a patient with its defaults already applied in Python.

        This exists because of a subtle and genuinely dangerous SQLAlchemy behaviour.
        ``mapped_column(default=True)`` is an *insert* default: it is applied when the row is
        flushed to the database, not when the object is constructed. So a freshly built
        ``Patient()`` has ``reminders_enabled = None`` until it is flushed.

        That matters because ``compute_status`` asks ``not patient.reminders_enabled`` — and
        ``not None`` is ``True``. Every unflushed patient would therefore be classified INACTIVE.

        The CSV importer builds patients and derives their status *before* flushing (it needs the
        derived values to be part of the same transaction), so this is not a hypothetical: a
        whole imported file would have arrived with every patient marked INACTIVE, and nobody
        would receive a reminder.

        Setting the defaults here makes a new object coherent immediately. SQLAlchemy does not
        call ``__init__`` when loading existing rows, so this affects construction only.
        """
        kwargs.setdefault("reminders_enabled", True)
        kwargs.setdefault("preferred_contact_method", PreferredContactMethod.EMAIL)
        kwargs.setdefault("status", PatientStatus.ACTIVE)
        super().__init__(**kwargs)

    # ---------------------------------------------------------------------------------------
    # Convenience properties
    #
    # These are presentation helpers only. No business rule lives on the model — status and date
    # logic belong to RecallService and nowhere else (SPEC §5).
    # ---------------------------------------------------------------------------------------

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self) -> str:
        """Used in the activity feed and in stored event payloads instead of a full name."""
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def is_contactable(self) -> bool:
        """Whether this patient may be sent a reminder at all.

        Combines the two independent ways reminders stop: the practice pausing them, and the
        patient opting out. Both must be clear.
        """
        return self.reminders_enabled and self.opted_out_at is None

    def __repr__(self) -> str:
        return f"<Patient {self.public_id} {self.status.value}>"
