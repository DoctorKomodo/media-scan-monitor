"""The DB migrates to head and the folder table carries every expected column."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from mediascanmonitor.db.session import init_db


def test_folder_table_has_library_name_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("folder")}
    assert "library_name" in columns


def test_server_table_has_webhook_payload_preset_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("server")}
    assert "webhook_payload_preset" in columns


def test_webhook_payload_preset_server_default_is_custom(tmp_path: Path) -> None:
    # A row inserted WITHOUT the column (as a pre-0003 row reads after migration) takes the
    # server_default 'custom' — this is the "existing rows keep current behaviour" guarantee.
    engine = init_db(tmp_path / "app.db")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO server (name, type, base_url, verify_tls, timeout_seconds, "
                "scan_mode, debounce_mode, debounce_window_seconds, retry_attempts, enabled) "
                "VALUES ('h', 'webhook', '', 1, 10.0, 'targeted', 'trailing', 30, 3, 1)"
            )
        )
        value = conn.execute(
            sa.text("SELECT webhook_payload_preset FROM server WHERE name = 'h'")
        ).scalar_one()
    assert value == "custom"


def test_server_table_has_event_types_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("server")}
    assert "event_types" in columns


def test_event_types_server_default_is_all_four(tmp_path: Path) -> None:
    # A row inserted WITHOUT the column (as a pre-0004 row reads after migration) takes the
    # server_default — every existing server keeps receiving every event type.
    engine = init_db(tmp_path / "app.db")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO server (name, type, base_url, verify_tls, timeout_seconds, "
                "scan_mode, debounce_mode, debounce_window_seconds, retry_attempts, enabled) "
                "VALUES ('h', 'webhook', '', 1, 10.0, 'targeted', 'trailing', 30, 3, 1)"
            )
        )
        value = conn.execute(
            sa.text("SELECT event_types FROM server WHERE name = 'h'")
        ).scalar_one()
    assert value == "created,moved_to,deleted,moved_from"


def _insert_minimal(conn: sa.Connection, table: str, **values: object) -> None:
    """Insert a row filling every NOT NULL, no-default column with a placeholder, so the
    helper works against the schema of whichever revision the DB is currently at."""
    for _cid, name, col_type, notnull, default, pk in conn.execute(
        sa.text(f"PRAGMA table_info({table})")
    ):
        if name in values or pk or not notnull or default is not None:
            continue
        values[name] = "x" if str(col_type).upper().startswith(("VARCHAR", "TEXT")) else 1
    cols = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    conn.execute(sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)


def _revisions_before_head() -> list[str]:
    from alembic.script import ScriptDirectory

    from mediascanmonitor.db.session import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config(":memory:"))
    head = script.get_current_head()
    return [rev.revision for rev in script.walk_revisions() if rev.revision != head]


@pytest.mark.parametrize("start", _revisions_before_head())
def test_upgrade_to_head_preserves_child_rows(tmp_path: Path, start: str) -> None:
    # A migration that rebuilds the `server` table (SQLite batch copy -> drop -> rename) must not
    # fire ON DELETE CASCADE and wipe every folder/filetype. Seed one row per table at each
    # historical revision, upgrade to head, and require all of them to survive.
    from alembic import command

    from mediascanmonitor.db.session import _alembic_config, create_db_engine

    db = tmp_path / "app.db"
    command.upgrade(_alembic_config(db), start)
    engine = create_db_engine(db)
    with engine.begin() as conn:
        _insert_minimal(conn, "server", id=1, name="plex", type="plex")
        _insert_minimal(conn, "folder", id=1, server_id=1, path="/media/tv")
        _insert_minimal(conn, "filetype", folder_id=1, extension="mkv")
    engine.dispose()

    command.upgrade(_alembic_config(db), "head")

    engine = create_db_engine(db)
    with engine.connect() as conn:
        counts = {
            table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("server", "folder", "filetype")
        }
    engine.dispose()
    assert counts == {"server": 1, "folder": 1, "filetype": 1}
