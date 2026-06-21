"""Authentication surface: Argon2 password hashing, Setting-backed storage, bootstrap.

All functions here are SYNCHRONOUS (Argon2 is CPU-bound, the Repo is sync SQLModel);
route handlers call them off the event loop via ``asyncio.to_thread`` (contract §C).
The password is stored as an Argon2 PHC string in the ``Setting`` table under
``password_hash`` — never in the clear. ``bootstrap_password`` seeds a first-run
password from the environment but NEVER logs the value (rule 5) and never overwrites a
password already set in the UI.
"""

import os
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from mediascanmonitor.db.repo import Repo

PASSWORD_HASH_KEY = "password_hash"

_hasher = PasswordHasher()  # library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError, InvalidHashError, VerificationError:
        return False


def is_password_set(repo: Repo) -> bool:
    return repo.get_setting(PASSWORD_HASH_KEY) is not None


def set_password(repo: Repo, password: str) -> None:
    repo.set_setting(PASSWORD_HASH_KEY, hash_password(password))


def check_password(repo: Repo, password: str) -> bool:
    stored = repo.get_setting(PASSWORD_HASH_KEY)
    return stored is not None and verify_password(stored, password)


def bootstrap_password(repo: Repo) -> None:
    """Seed a first-run password from the environment (idempotent).

    Precedence: ``MSM_PASSWORD_FILE`` (a path; file contents, whitespace-stripped) then
    ``MSM_PASSWORD``. If a password is already set, return without touching it. If neither
    env source yields a non-empty value, do nothing — the setup screen handles first run.
    Never logs the value.
    """
    if is_password_set(repo):
        return
    value = ""
    file_path = os.environ.get("MSM_PASSWORD_FILE")
    if file_path:
        try:
            value = _read_secret_file(file_path)
        except OSError:
            value = ""
    if not value:
        value = (os.environ.get("MSM_PASSWORD") or "").strip()
    if value:
        set_password(repo, value)


def _read_secret_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()
