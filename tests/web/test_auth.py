"""web/auth.py password surface: Argon2 hashing, Setting-backed password, env bootstrap."""

from pathlib import Path

import pytest
import structlog

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


def test_bootstrap_no_env_now_autogenerates(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MSM_PASSWORD", "MSM_PASSWORD_FILE", "MSM_INITIAL_PASSWORD_FILE", "MSM_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    auth.bootstrap_password(repo, initial_password_path=tmp_path / "initial_password.txt")
    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)


def test_bootstrap_empty_env_values_autogenerate(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSM_PASSWORD", "   ")  # whitespace-only → treated as unset
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    auth.bootstrap_password(repo, initial_password_path=tmp_path / "initial_password.txt")
    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)


def test_generate_password_is_strong_and_unique() -> None:
    a = auth.generate_password()
    b = auth.generate_password()
    assert a != b
    assert len(a) >= 20
    assert a.strip() == a  # url-safe, no surrounding whitespace


def test_bootstrap_autogenerates_when_no_env(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MSM_PASSWORD", "MSM_PASSWORD_FILE", "MSM_INITIAL_PASSWORD_FILE", "MSM_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    pw_file = tmp_path / "initial_password.txt"

    with structlog.testing.capture_logs() as logs:
        auth.bootstrap_password(repo, initial_password_path=pw_file)

    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)
    # the file holds the live password, restricted to the owner
    assert pw_file.exists()
    assert (pw_file.stat().st_mode & 0o777) == 0o600
    generated = pw_file.read_text(encoding="utf-8").strip()
    assert auth.check_password(repo, generated)
    # rule 5: the path is logged, the value never is
    events = [e for e in logs if e.get("event") == "auth.bootstrap.generated"]
    assert events and events[0]["path"] == str(pw_file)
    assert all(generated not in str(e) for e in logs)


def test_bootstrap_env_var_skips_autogen(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("MSM_PASSWORD", "chosen-pw")
    pw_file = tmp_path / "initial_password.txt"
    auth.bootstrap_password(repo, initial_password_path=pw_file)
    assert auth.check_password(repo, "chosen-pw")
    assert not auth.is_must_change(repo)
    assert not pw_file.exists()  # env-supplied password writes no file


def test_clear_initial_password_clears_flag_and_deletes_file(repo: Repo, tmp_path: Path) -> None:
    repo.set_setting(auth.MUST_CHANGE_KEY, "1")
    pw_file = tmp_path / "initial_password.txt"
    pw_file.write_text("x\n", encoding="utf-8")
    auth.clear_initial_password(repo, pw_file)
    assert not auth.is_must_change(repo)
    assert not pw_file.exists()


def test_clear_initial_password_tolerates_missing_file(repo: Repo, tmp_path: Path) -> None:
    repo.set_setting(auth.MUST_CHANGE_KEY, "1")
    auth.clear_initial_password(repo, tmp_path / "nope.txt")  # must not raise
    assert not auth.is_must_change(repo)
