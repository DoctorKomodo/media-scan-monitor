"""Shared fixtures for repo tests: a real file-backed SQLite DB under tmp_path."""

from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session

from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.session import init_db, session_factory


@pytest.fixture
def box() -> SecretBox:
    return SecretBox(Fernet.generate_key())


@pytest.fixture
def factory(tmp_path: Path) -> Callable[[], Session]:
    engine = init_db(tmp_path / "app.db")
    return session_factory(engine)


@pytest.fixture
def repo(factory: Callable[[], Session], box: SecretBox) -> Repo:
    return Repo(factory, box)
