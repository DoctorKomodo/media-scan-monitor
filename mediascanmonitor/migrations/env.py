"""Alembic environment (online-only; the app never runs `alembic upgrade --sql`)."""

from alembic import context
from sqlalchemy import engine_from_config, event, pool
from sqlmodel import SQLModel

# Import models so SQLModel.metadata is fully populated for autogenerate + the sync test.
from mediascanmonitor.db import models  # noqa: F401

target_metadata = SQLModel.metadata


def _set_sqlite_fk(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def run_migrations_online():
    cfg = context.config
    connectable = engine_from_config(
        cfg.get_section(cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", _set_sqlite_fk)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite: emulate limited ALTER TABLE via batch copy
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
