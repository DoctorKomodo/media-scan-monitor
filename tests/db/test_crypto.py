"""Tests for SecretBox / load_or_create_key (contract section 3)."""

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from mediascanmonitor.db.crypto import (
    SecretBox,
    SecretDecryptError,
    load_or_create_key,
)


def test_encrypt_decrypt_round_trip() -> None:
    box = SecretBox(Fernet.generate_key())
    token = box.encrypt("super-secret-token")
    assert token != "super-secret-token"
    assert box.decrypt(token) == "super-secret-token"


def test_decrypt_bad_token_raises_secret_decrypt_error() -> None:
    box = SecretBox(Fernet.generate_key())
    with pytest.raises(SecretDecryptError):
        box.decrypt("not-a-valid-fernet-token")


def test_decrypt_with_wrong_key_raises_secret_decrypt_error() -> None:
    token = SecretBox(Fernet.generate_key()).encrypt("x")
    other = SecretBox(Fernet.generate_key())
    with pytest.raises(SecretDecryptError):
        other.decrypt(token)


def test_load_or_create_key_env_takes_precedence(tmp_path: Path) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / "secret.key"
    result = load_or_create_key(key_path, env_key=env_key)
    assert result == env_key.encode("ascii")
    assert not key_path.exists()  # env wins; nothing written to disk


def test_load_or_create_key_reads_existing_file(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    key_path = tmp_path / "secret.key"
    key_path.write_bytes(key)
    assert load_or_create_key(key_path) == key


def test_load_or_create_key_strips_file_whitespace(tmp_path: Path) -> None:
    # a key file written with a trailing newline (Docker secret / editor) must load cleanly
    key = Fernet.generate_key()
    key_path = tmp_path / "secret.key"
    key_path.write_bytes(key + b"\n")
    assert load_or_create_key(key_path) == key


def test_load_or_create_key_generates_file_with_mode_0600(tmp_path: Path) -> None:
    key_path = tmp_path / "sub" / "secret.key"
    key = load_or_create_key(key_path)
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    # the generated key must be a usable Fernet key
    box = SecretBox(key)
    assert box.decrypt(box.encrypt("value")) == "value"


def test_load_or_create_key_strips_env_whitespace(tmp_path: Path) -> None:
    # a trailing newline (Docker secret / `$(cat ...)`) must not corrupt the key
    env_key = Fernet.generate_key().decode("ascii")
    result = load_or_create_key(tmp_path / "unused.key", env_key=env_key + "\n")
    assert result == env_key.encode("ascii")
