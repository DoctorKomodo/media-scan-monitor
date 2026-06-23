"""The DB migrates to head and the folder table carries every expected column."""

from pathlib import Path

from sqlalchemy import inspect

from mediascanmonitor.db.session import init_db


def test_folder_table_has_library_name_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("folder")}
    assert "library_name" in columns
