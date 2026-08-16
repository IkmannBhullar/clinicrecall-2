"""Declarative base and the mixins every table shares.

Three small pieces live here so that no table has to restate them:

* ``Base``               — the SQLAlchemy 2.x declarative base all models inherit from.
* ``UUIDPrimaryKeyMixin`` — a random UUID primary key.
* ``TimestampMixin``      — ``created_at`` / ``updated_at``, maintained by the database.

Keeping these in one place means a new table cannot accidentally get a slightly different
timestamp behaviour from every other table, which is the kind of inconsistency that turns into a
confusing bug six months later.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit, predictable names for indexes and constraints.
#
# Without this, Postgres invents names and Alembic has to guess them when generating a migration
# that drops or alters a constraint — which is exactly when a wrong guess is most expensive. The
# `ix`/`uq`/`ck`/`fk`/`pk` prefixes also make a `\d patients` in psql readable at a glance.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ClinicRecall table.

    Alembic reads ``Base.metadata`` to work out what the schema should look like, so a model that
    does not inherit from this will silently not appear in any migration.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """Adds a random UUID primary key.

    UUIDs rather than auto-incrementing integers because a sequential key leaks information — how
    many patients a clinic has, and in what order they were added — to anyone who sees one. These
    keys stay internal (SPEC §4.2 uses ``public_id`` for anything outward-facing), but defaulting
    to the safer option costs nothing.

    Generated in Python rather than by the database so that code can hold a new object's ID before
    it is flushed, which makes building related rows in one transaction much simpler.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at``, both maintained by the database.

    ``server_default``/``onupdate`` rather than Python defaults, so the values are correct even
    for rows written by a migration, a bulk import, or a hand-run SQL statement. A timestamp that
    is only correct when the application happens to be the writer is not much of an audit trail.

    Both are timezone-aware (``timestamptz``). Storing a naive local time is the single most
    common way date handling goes wrong, and this product's entire value proposition rests on
    getting dates right.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
