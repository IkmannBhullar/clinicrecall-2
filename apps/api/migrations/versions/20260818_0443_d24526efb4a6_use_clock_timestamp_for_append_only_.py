"""use clock_timestamp for append only tables

Revision ID: d24526efb4a6
Revises: 3f08e366b208
Created: 2026-08-18 04:43

PostgreSQL's ``now()`` returns the **transaction** start time, not the moment the statement runs.
Every row written inside one transaction therefore receives a byte-identical ``created_at``.

That is fine for a timestamp meaning "roughly when this happened", and wrong for anything that
orders rows against each other. Two places in this application do exactly that:

* The failure-recovery queue asks "has a later reminder to this patient succeeded?" — and with
  transaction-scoped timestamps, a corrected resend written in the same request is not *later*
  than the failure it replaces. The queue would never empty.
* The activity feed sorts newest-first. A reminder job creating fifty events would give all fifty
  the same timestamp, leaving their order arbitrary.

``clock_timestamp()`` returns the actual wall-clock reading at the moment of the statement, which
is what these columns were always meant to hold.

Applied only to the append-only tables. ``created_at``/``updated_at`` on the mutable tables keep
``now()``: nothing orders rows within a transaction there, and transaction-scoped timestamps are
arguably more truthful for "when was this record created".
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic to order migrations.
revision: str = "d24526efb4a6"
down_revision: str | None = "3f08e366b208"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Append-only tables whose rows are ordered against each other.
EVENT_TABLES = ("reminder_events", "activity_events")


def upgrade() -> None:
    for table in EVENT_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT clock_timestamp()")


def downgrade() -> None:
    for table in EVENT_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT now()")
