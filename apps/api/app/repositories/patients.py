"""Patient queries.

The busiest repository in the application. Everything the patients list, the dashboard, the
reminder job, and the CSV importer need to read about patients is expressed here — and every one
of those queries starts from ``self._scoped_select(organization_id)``, so none of them can return
another practice's records.

Phases 3 and beyond add the recall-specific queries (due windows, status recomputation targets).
What is here now is the foundation those build on.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, or_, select

from app.models.enums import PatientStatus
from app.models.patient import Patient
from app.repositories.base import OrganizationScopedRepository


class PatientRepository(OrganizationScopedRepository[Patient]):
    """Reads and writes the ``patients`` table."""

    model = Patient

    # -----------------------------------------------------------------------------------------
    # Identity lookups
    # -----------------------------------------------------------------------------------------

    def get_by_public_id(self, organization_id: uuid.UUID, public_id: str) -> Patient | None:
        """Fetch a patient by the identifier used in URLs and API responses (SPEC §4.2).

        Still scoped by organization even though ``public_id`` is globally unique. Uniqueness
        means the lookup would succeed without the scope — which is exactly why the scope is
        there. A stray public ID from another practice must return nothing, not a record.
        """
        statement = self._scoped_select(organization_id).where(Patient.public_id == public_id)
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_external_id(self, organization_id: uuid.UUID, external_id: str) -> Patient | None:
        """Fetch by the practice's own identifier — the first dedupe key for CSV import."""
        statement = self._scoped_select(organization_id).where(Patient.external_id == external_id)
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_email(self, organization_id: uuid.UUID, email: str) -> Patient | None:
        """Fetch by email — the second dedupe key for CSV import (SPEC §7.1).

        ``func.lower`` on both sides matches the unique index defined on the table, so this query
        uses that index rather than falling back to a sequential scan.
        """
        statement = self._scoped_select(organization_id).where(
            func.lower(Patient.email) == email.lower()
        )
        return self.session.execute(statement).scalar_one_or_none()

    # -----------------------------------------------------------------------------------------
    # List queries
    # -----------------------------------------------------------------------------------------

    def search(
        self,
        organization_id: uuid.UUID,
        *,
        query: str | None = None,
        statuses: Sequence[PatientStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Patient]:
        """The patients list: free-text search plus status filters, paginated.

        Pagination is applied in SQL rather than in Python. Loading every patient and slicing the
        result would work fine for the fifty-five in the demo and fall over for a real practice —
        the sort of thing that is invisible until it is a production incident.
        """
        statement = self._scoped_select(organization_id)

        if query:
            # Match against first name, last name, or email. `ilike` is Postgres' case-insensitive
            # LIKE, which is what someone typing "sarah" into a search box expects.
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    Patient.first_name.ilike(pattern),
                    Patient.last_name.ilike(pattern),
                    Patient.email.ilike(pattern),
                )
            )

        if statuses:
            statement = statement.where(Patient.status.in_(statuses))

        # Most-urgent first: the soonest due date at the top is the order staff actually work in.
        statement = statement.order_by(Patient.next_annual_due_date.asc(), Patient.last_name.asc())

        return self.session.execute(statement.limit(limit).offset(offset)).scalars().all()

    def count_search(
        self,
        organization_id: uuid.UUID,
        *,
        query: str | None = None,
        statuses: Sequence[PatientStatus] | None = None,
    ) -> int:
        """Total matching rows, for the pagination control.

        Mirrors ``search``'s filters exactly but without ordering or limits. The two must be kept
        in step; if they drift, the list shows "page 4 of 7" and page 4 is empty.
        """
        statement = select(func.count()).select_from(Patient)
        statement = self._scope(statement, organization_id)

        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    Patient.first_name.ilike(pattern),
                    Patient.last_name.ilike(pattern),
                    Patient.email.ilike(pattern),
                )
            )

        if statuses:
            statement = statement.where(Patient.status.in_(statuses))

        return self.session.execute(statement).scalar_one()

    def list_by_status(
        self, organization_id: uuid.UUID, status: PatientStatus
    ) -> Sequence[Patient]:
        statement = self._scoped_select(organization_id).where(Patient.status == status)
        return self.session.execute(statement).scalars().all()

    def count_by_status(self, organization_id: uuid.UUID) -> dict[PatientStatus, int]:
        """One row per status with its count — the dashboard's Recall Overview.

        A single grouped query rather than seven separate counts. Statuses with no patients are
        filled in as zero so the caller always gets a complete picture and never has to guard
        against a missing key.
        """
        statement = (
            select(Patient.status, func.count())
            .where(self._organization_filter(organization_id))
            .group_by(Patient.status)
        )
        counts = {status: 0 for status in PatientStatus}
        for status, count in self.session.execute(statement).all():
            counts[status] = count
        return counts

    def list_due_between(
        self, organization_id: uuid.UUID, start: date, end: date
    ) -> Sequence[Patient]:
        """Patients whose next annual visit falls in an inclusive date window.

        Used by the dashboard's "Due This Month" figure and by the reminder job when working out
        who is eligible today.
        """
        statement = self._scoped_select(organization_id).where(
            Patient.next_annual_due_date >= start,
            Patient.next_annual_due_date <= end,
        )
        return self.session.execute(statement).scalars().all()

    # -----------------------------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------------------------

    def create(self, organization_id: uuid.UUID, patient: Patient) -> Patient:
        """Persist a new patient.

        Takes a fully-built ``Patient`` rather than a long list of keyword arguments, because
        constructing one correctly means deriving ``next_annual_due_date`` and ``status`` — and
        that derivation belongs to ``RecallService``, not to a repository (SPEC §5.1). A
        repository that computed those values would be a second place where status logic lives,
        which the spec explicitly forbids.
        """
        return self.add(organization_id, patient)
