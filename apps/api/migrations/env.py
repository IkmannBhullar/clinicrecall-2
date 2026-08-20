"""Alembic environment.

Two things here are worth understanding rather than skimming:

1. **The database URL comes from the application's settings**, not from alembic.ini. One source
   of truth means migrations always run against the same database the app talks to.

2. **``include_object`` restricts Alembic to the ``public`` schema.** The local Supabase stack
   puts its own tables in ``auth``, ``storage``, and ``realtime``. Without this filter,
   autogenerate would compare our models against *every* table in the database, decide that
   GoTrue's tables are unexpected, and cheerfully generate a migration that drops the
   authentication system. SPEC §3.1 states the rule; this function is where it is enforced.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (imported for its side effect: registering all tables)
from app.core.config import settings

# Importing the package registers every model on Base.metadata. A model that is never imported is
# invisible to autogenerate, so this import is load-bearing despite looking unused.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the real connection string, overriding anything alembic.ini might carry.
#
# The percent signs are doubled because set_main_option writes into alembic.ini's ConfigParser,
# which treats "%" as interpolation syntax. A hosted Postgres password containing a character that
# must be percent-encoded in a URL — "@" becomes "%40", and Supabase generates passwords like this
# routinely — otherwise raises "invalid interpolation syntax" before a single migration runs.
# SQLAlchemy receives the original string, because ConfigParser un-doubles it on read.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata

# Schemas that belong to Supabase, not to us. Alembic must be blind to all of them.
NON_APPLICATION_SCHEMAS = {
    "auth",  # GoTrue — user accounts, sessions, refresh tokens
    "storage",  # Supabase Storage
    "realtime",  # Supabase Realtime
    "graphql",
    "graphql_public",
    "extensions",
    "pgbouncer",
    "vault",
    "supabase_functions",
    "supabase_migrations",
    "_realtime",
    "_analytics",
    "pgsodium",
    "pgsodium_masks",
    "net",
    "cron",
}


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Decide whether Alembic should consider a database object.

    Returning False makes an object invisible: Alembic will neither create it, alter it, nor —
    critically — generate a DROP for it.
    """
    schema = getattr(obj, "schema", None)
    if schema in NON_APPLICATION_SCHEMAS:
        return False

    # Supabase's CLI keeps its own migration bookkeeping inside the database. Not ours to manage.
    return not (type_ == "table" and name in {"schema_migrations", "seed_files"})


def run_migrations_offline() -> None:
    """Generate SQL without connecting to a database (``alembic upgrade head --sql``).

    Useful for reviewing exactly what a migration will do before letting it near real data.
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        include_schemas=False,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            # Never reflect other schemas. Belt and braces alongside include_object.
            include_schemas=False,
            # Notice when a column's type changes, not just when columns appear or disappear.
            compare_type=True,
            # Notice changes to server-side defaults too.
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
