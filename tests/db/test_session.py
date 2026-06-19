"""Tests for init_db (Alembic upgrade) / session_factory (contract §2/§4)."""

from pathlib import Path

from mediascanmonitor.db.models import Setting
from mediascanmonitor.db.session import init_db, resolve_db_path, session_factory
from sqlalchemy import inspect
from sqlmodel import SQLModel


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")
    tables = set(inspect(engine).get_table_names())
    assert {"server", "folder", "filetype", "setting"} <= tables
    assert "alembic_version" in tables  # Alembic tracks the applied revision


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    init_db(db)
    engine = init_db(db)  # second upgrade is a no-op (already at head)
    assert "server" in set(inspect(engine).get_table_names())


def test_migrations_match_models(tmp_path: Path) -> None:
    # The migration chain must produce exactly the schema SQLModel declares — catches a
    # revision that forgot a column/table. An empty diff means in-sync.
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = init_db(tmp_path / "app.db")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diff = compare_metadata(ctx, SQLModel.metadata)
    assert diff == [], f"migrations drifted from models: {diff}"


def test_session_factory_shares_engine_state(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")
    factory = session_factory(engine)
    with factory() as writer:
        writer.add(Setting(key="k", value="v"))
        writer.commit()
    with factory() as reader:
        assert reader.get(Setting, "k") is not None


def test_resolve_db_path_precedence(tmp_path: Path, monkeypatch) -> None:
    explicit = tmp_path / "explicit.db"
    assert resolve_db_path(explicit) == explicit

    monkeypatch.setenv("MSM_DB_PATH", str(tmp_path / "env.db"))
    assert resolve_db_path() == tmp_path / "env.db"

    monkeypatch.delenv("MSM_DB_PATH", raising=False)
    assert resolve_db_path() == Path("/config/app.db")
