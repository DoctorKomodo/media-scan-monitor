"""Alembic environment (online-only; the app never runs `alembic upgrade --sql`)."""

from alembic import context
from sqlalchemy import engine_from_config, event, pool
from sqlmodel import SQLModel

# Import models so SQLModel.metadata is fully populated for autogenerate + the sync test.
from mediascanmonitor.db import models  # noqa: F401

target_metadata = SQLModel.metadata


def _disable_sqlite_fk(dbapi_connection, connection_record):
    # Foreign keys stay OFF while migrating. SQLite batch mode rebuilds a table by copy ->
    # DROP -> rename; with FKs ON, dropping the parent (e.g. `server`) fires ON DELETE CASCADE
    # and silently deletes every child row (folders, filetypes). The pragma is a no-op inside a
    # transaction, so it must be set here, at connect time. Integrity is re-checked below.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


def _assert_foreign_keys_intact(connection) -> None:
    # Fail the migration (rolling back the transaction) rather than commit dangling references.
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"migration left foreign-key violations: {violations}")


def run_migrations_online():
    cfg = context.config
    connectable = engine_from_config(
        cfg.get_section(cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", _disable_sqlite_fk)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite: emulate limited ALTER TABLE via batch copy
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            _assert_foreign_keys_intact(connection)


run_migrations_online()
