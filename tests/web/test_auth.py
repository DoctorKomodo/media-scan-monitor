"""web/auth.py password surface: Argon2 hashing, Setting-backed password, env bootstrap."""

from pathlib import Path

import pytest

# `repo` fixture comes from tests/web/conftest.py (Task 4). For this function-level test
# file it is equally satisfied by the same fixture; conftest lands in Task 4, so run this
# file's tests only after Task 4's conftest exists OR add a local repo fixture. To keep
# Task 3 self-contained, this file defines its own minimal repo fixture mirroring
# tests/db/conftest.py.
from cryptography.fernet import Fernet

from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.session import init_db, session_factory
from mediascanmonitor.web import auth


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    engine = init_db(tmp_path / "app.db")
    return Repo(session_factory(engine), SecretBox(Fernet.generate_key()))


def test_hash_and_verify_roundtrip() -> None:
    h = auth.hash_password("hunter2")
    assert h != "hunter2"  # never stored in the clear
    assert h.startswith("$argon2")  # Argon2 PHC string
    assert auth.verify_password(h, "hunter2") is True
    assert auth.verify_password(h, "wrong") is False


def test_verify_password_never_raises_on_garbage_hash() -> None:
    assert auth.verify_password("not-a-hash", "anything") is False
    assert auth.verify_password("", "anything") is False


def test_is_password_set_false_then_true(repo: Repo) -> None:
    assert auth.is_password_set(repo) is False
    auth.set_password(repo, "pw")
    assert auth.is_password_set(repo) is True


def test_check_password(repo: Repo) -> None:
    assert auth.check_password(repo, "pw") is False  # nothing set yet
    auth.set_password(repo, "pw")
    assert auth.check_password(repo, "pw") is True
    assert auth.check_password(repo, "nope") is False


def test_set_password_overwrites(repo: Repo) -> None:
    auth.set_password(repo, "first")
    auth.set_password(repo, "second")
    assert auth.check_password(repo, "second") is True
    assert auth.check_password(repo, "first") is False


def test_bootstrap_noop_when_already_set(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    auth.set_password(repo, "ui-set")
    monkeypatch.setenv("MSM_PASSWORD", "env-set")
    auth.bootstrap_password(repo)  # idempotent — must NOT overwrite the UI password
    assert auth.check_password(repo, "ui-set") is True
    assert auth.check_password(repo, "env-set") is False


def test_bootstrap_from_env_var(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("MSM_PASSWORD", "from-env")
    auth.bootstrap_password(repo)
    assert auth.check_password(repo, "from-env") is True


def test_bootstrap_file_takes_precedence_over_var(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("  from-file\n")  # whitespace must be stripped
    monkeypatch.setenv("MSM_PASSWORD_FILE", str(pw_file))
    monkeypatch.setenv("MSM_PASSWORD", "from-env")
    auth.bootstrap_password(repo)
    assert auth.check_password(repo, "from-file") is True
    assert auth.check_password(repo, "from-env") is False


def test_bootstrap_does_nothing_without_env(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("MSM_PASSWORD", raising=False)
    auth.bootstrap_password(repo)
    assert auth.is_password_set(repo) is False  # first-run setup screen will handle it


def test_bootstrap_ignores_empty_values(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n")  # whitespace-only file → empty after strip
    monkeypatch.setenv("MSM_PASSWORD_FILE", str(blank))
    monkeypatch.setenv("MSM_PASSWORD", "")  # empty var
    auth.bootstrap_password(repo)
    assert auth.is_password_set(repo) is False
