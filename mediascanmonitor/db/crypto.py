"""Secret encryption at rest using Fernet (contract section 3).

`SecretBox` wraps a single Fernet key. `load_or_create_key` resolves the key with the
precedence env_key > file at path > generate-and-write (chmod 0600). The plaintext of a
secret only ever exists transiently inside `encrypt`/`decrypt`.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptError(Exception):
    """Raised when a stored secret cannot be decrypted (bad token or wrong key)."""


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretDecryptError("could not decrypt secret") from exc


def load_or_create_key(path: Path, env_key: str | None = None) -> bytes:
    """Return a urlsafe-base64 Fernet key.

    Precedence: ``env_key`` (the key value itself) > the file at ``path`` >
    generate a new key and create ``path`` atomically with mode 0600.
    ``env_key`` and file contents are stripped of surrounding whitespace (a trailing
    newline from a Docker secret / ``$(cat ...)`` would otherwise corrupt the key).
    """
    if env_key:
        return env_key.strip().encode("ascii")
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    # create atomically with 0600 — no write-then-chmod window where the key is world-readable
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)  # force exact mode regardless of umask
        os.write(fd, key)
    finally:
        os.close(fd)
    return key
