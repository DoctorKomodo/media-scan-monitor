"""SQLite engine, Alembic migration runner, and session factory.

`init_db` creates the engine and migrates the DB to ``head`` via Alembic (the explicit
migration step required by rule 7 — no `create_all`). Sessions are built with
`expire_on_commit=False` so ORM instances returned from `Repo` methods stay usable after
their session closes. Foreign keys are enforced per connection (`PRAGMA foreign_keys=ON`).
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

DEFAULT_DB_PATH = "/config/app.db"
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _enable_sqlite_fk(dbapi_connection: Any, connection_record: Any) -> None:
    """SQLite ignores FOREIGN KEY constraints unless this is set per connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the DB path. Precedence: explicit arg > ``MSM_DB_PATH`` env > default."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("MSM_DB_PATH")
    if env:
        return Path(env)
    return Path(DEFAULT_DB_PATH)


def create_db_engine(db_path: str | os.PathLike[str]) -> Engine:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_sqlite_fk)  # enforce FKs on every connection
    return engine


def _alembic_config(db_path: str | os.PathLike[str]) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_path)}")
    return cfg


def init_db(db_path: str | os.PathLike[str]) -> Engine:
    """Create the engine and migrate the DB to ``head`` (Alembic). Idempotent."""
    engine = create_db_engine(db_path)
    command.upgrade(_alembic_config(db_path), "head")
    return engine


def session_factory(engine: Engine) -> Callable[[], Session]:
    """Return a zero-arg callable producing fresh sessions bound to ``engine``."""

    def factory() -> Session:
        return Session(engine, expire_on_commit=False)

    return factory
