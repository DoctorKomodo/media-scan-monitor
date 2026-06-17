# Phase 1 — Sub-plan 01: DB & Crypto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the persistence foundation for `media-scan-monitor` — SQLModel tables + enums, Pydantic boundary schemas, Fernet secret crypto, the SQLite session/migration layer, and a sync `Repo` — exactly as fixed by the frozen interface contract (sections 1–4).

**Architecture:** Five focused modules under `mediascanmonitor/db/` (`models`, `schemas`, `crypto`, `session`, `repo`) plus a two-function stub in `mediascanmonitor/config/defaults.py`. Secrets are encrypted with Fernet and never stored or returned in plaintext except via `Repo.resolve_secret`. Sessions are created from a `session_factory` bound to a single SQLite engine; `init_db` runs `create_all` and seeds the `schema_version` setting. All repo methods are **synchronous** (per contract conventions; sub-plan 06 wraps them in `asyncio.to_thread`).

**Tech Stack:** Python ≥ 3.14, `sqlmodel==0.0.38`, `cryptography==49.0.0` (Fernet), `pydantic==2.13.4`, dev: `pytest==9.1.0`, `pytest-asyncio==1.4.0`. `mypy --strict` clean, `ruff` clean, line length 100, `from __future__ import annotations` in every module.

---

## Architecture note — frozen-contract & cross-plan dependency

This plan **consumes** the frozen interface contract
(`docs/superpowers/plans/2026-06-17-phase1-00-interface-contract.md`) verbatim. The enums
(`ServerType`, `ScanMode`, `DebounceMode`), the SQLModel tables (`Server`, `Folder`, `FileType`,
`Setting`), `SecretBox` / `SecretDecryptError` / `load_or_create_key`, the `Repo` method
signatures, and `ServerCreate` / `ServerUpdate` / `FolderCreate` are **not** redefined or renamed
here — they are reproduced exactly as specified in contract sections 1–4.

**Cross-plan dependency (read before starting):** the repo normalizes extensions and paths via
`normalize_extension` / `normalize_path`, which the contract (section 6) assigns to
`mediascanmonitor/config/defaults.py` — **owned by sub-plan 02**, not this one. To keep this
sub-plan independently buildable and testable, **Task 0** adds *only* those two pure functions as
a clearly-marked stub. When sub-plan 02 lands (`IGNORE_DIRS`, `EXTENSION_PRESETS`, debounce
defaults, and the full absolute-path `normalize_path`), that module must be **reconciled**: keep
the function names/signatures identical and merge sub-plan 02's richer bodies in. The two
functions are pure and tiny, so the reconciliation is mechanical.

**SQLite session note:** tests use a file-based DB under `tmp_path` (not `sqlite://` in-memory)
so that the per-call sessions produced by `session_factory` share state. The session factory
builds sessions with `expire_on_commit=False`, so ORM instances returned from repo methods remain
usable (attributes already loaded) after their session closes — this is why no `session.refresh`
gymnastics are needed.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/config/defaults.py` | **(stub; owned by sub-plan 02)** `normalize_extension`, `normalize_path` pure helpers |
| `mediascanmonitor/db/models.py` | `ServerType`/`ScanMode`/`DebounceMode` enums + `Server`/`Folder`/`FileType`/`Setting` SQLModel tables |
| `mediascanmonitor/db/crypto.py` | `SecretBox`, `SecretDecryptError`, `load_or_create_key` (Fernet) |
| `mediascanmonitor/db/session.py` | `init_db(db_path)`, `session_factory(engine)`, `resolve_db_path`, `SCHEMA_VERSION` |
| `mediascanmonitor/db/schemas.py` | Pydantic `ServerCreate`, `ServerUpdate`, `FolderCreate` (plaintext `secret` in) |
| `mediascanmonitor/db/repo.py` | `Repo` — server/folder/filetype CRUD, `resolve_secret`, settings |
| `tests/db/__init__.py` | test package marker |
| `tests/db/conftest.py` | `box` / `factory` / `repo` fixtures |
| `tests/db/test_*.py` | unit tests per module |

---

### Task 0: `config/defaults.py` normalization stub (owned by sub-plan 02 — reconcile)

> **⚠️ SKIP THIS TASK in the canonical Phase 1 order.** Per
> `2026-06-17-phase1-README.md`, sub-plan **02** owns `config/defaults.py` and is built
> *before* this plan's repo task, so the module (with the full `normalize_extension` /
> `normalize_path` plus presets and debounce defaults) already exists. Run Task 0 **only** if
> you are executing sub-plan 01 standalone, before sub-plan 02 — in which case sub-plan 02's
> Task 1 ("Create `config/defaults.py`") becomes a "Modify" (merge the richer bodies onto these
> identical signatures).

**Files:**
- Create: `mediascanmonitor/config/defaults.py`
- Create: `tests/config/__init__.py`
- Test: `tests/config/test_defaults.py`

> The package `mediascanmonitor/config/__init__.py` already exists (Phase 0 skeleton). This task
> adds **only** `normalize_extension` and `normalize_path`. Do not add `IGNORE_DIRS` etc. here —
> those belong to sub-plan 02 and would create merge churn.
>
> `tests/__init__.py` already exists, so `tests/` is an importable package; every test
> subdirectory therefore also needs an `__init__.py` (this task adds `tests/config/__init__.py`;
> Task 1 adds `tests/db/__init__.py`) to keep pytest's package import mode happy.

- [ ] **Step 1: Write the failing test**

Create `tests/config/__init__.py` (empty package marker):

```python
```

Create `tests/config/test_defaults.py`:

```python
"""Tests for the path/extension normalization stubs (reconciled by sub-plan 02)."""

from __future__ import annotations

from mediascanmonitor.config.defaults import normalize_extension, normalize_path


def test_normalize_extension_strips_dot_lowercases_and_trims() -> None:
    assert normalize_extension(".MKV") == "mkv"
    assert normalize_extension("MP4") == "mp4"
    assert normalize_extension(" .Srt ") == "srt"
    assert normalize_extension("mkv") == "mkv"


def test_normalize_path_collapses_separators_and_trailing_slash() -> None:
    assert normalize_path("/data/tv/") == "/data/tv"
    assert normalize_path("/data/tv") == "/data/tv"
    assert normalize_path("/data//tv/") == "/data/tv"
    assert normalize_path("/") == "/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_defaults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.config.defaults'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/config/defaults.py`:

```python
"""Pure path/extension normalization helpers.

NOTE: This module is OWNED by sub-plan 02 (`config/defaults.py`), which will add
`IGNORE_DIRS`, `EXTENSION_PRESETS`, the per-type debounce defaults, and the full
absolute-path `normalize_path`. Sub-plan 01 only needs `normalize_extension` and
`normalize_path`, so this stub adds just those two pure functions. When sub-plan 02
lands, RECONCILE this file: keep these signatures and merge in the richer bodies.
"""

from __future__ import annotations

import os


def normalize_extension(ext: str) -> str:
    """Strip leading dot(s), lowercase, strip surrounding whitespace. ``".MKV"`` -> ``"mkv"``."""
    return ext.strip().lstrip(".").strip().lower()


def normalize_path(path: str) -> str:
    """Collapse redundant separators and the trailing slash (except root)."""
    return os.path.normpath(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_defaults.py -v`
Expected: PASS — `2 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/config/defaults.py tests/config/__init__.py tests/config/test_defaults.py
git commit -m "feat(config): add normalize_extension/normalize_path stubs (sub-plan 02 reconcile)"
```

---

### Task 1: `db/models.py` — enums + SQLModel tables

**Files:**
- Create: `mediascanmonitor/db/models.py`
- Create: `tests/db/__init__.py`
- Test: `tests/db/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/__init__.py` (empty package marker):

```python
```

Create `tests/db/test_models.py`:

```python
"""Tests for the SQLModel tables and enums (contract sections 1-2)."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from mediascanmonitor.db.models import (
    DebounceMode,
    FileType,
    Folder,
    ScanMode,
    Server,
    ServerType,
    Setting,
)


def _memory_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_enum_values_match_contract() -> None:
    assert ServerType.webhook.value == "webhook"
    assert ServerType.plex.value == "plex"
    assert ServerType.emby.value == "emby"
    assert ServerType.jellyfin.value == "jellyfin"
    assert ServerType.audiobookshelf.value == "audiobookshelf"
    assert ScanMode.targeted.value == "targeted"
    assert ScanMode.library.value == "library"
    assert DebounceMode.off.value == "off"
    assert DebounceMode.trailing.value == "trailing"


def test_server_defaults() -> None:
    server = Server(name="plex1", type=ServerType.plex)
    assert server.base_url == ""
    assert server.verify_tls is True
    assert server.timeout_seconds == 10.0
    assert server.secret_encrypted is None
    assert server.scan_mode is ScanMode.targeted
    assert server.debounce_mode is DebounceMode.trailing
    assert server.debounce_window_seconds == 30
    assert server.retry_attempts == 3
    assert server.enabled is True


def test_relationships_persist() -> None:
    engine = _memory_engine()
    with Session(engine) as session:
        server = Server(name="plex1", type=ServerType.plex)
        folder = Folder(server=server, path="/data/tv", library_id="2")
        folder.filetypes.extend(
            [FileType(extension="mkv"), FileType(extension="srt")],
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        assert server.id is not None
        assert len(server.folders) == 1
        assert server.folders[0].server_id == server.id
        assert {ft.extension for ft in server.folders[0].filetypes} == {"mkv", "srt"}


def test_cascade_delete_removes_folders_and_filetypes() -> None:
    engine = _memory_engine()
    with Session(engine) as session:
        server = Server(name="plex1", type=ServerType.plex)
        folder = Folder(server=server, path="/data/tv")
        folder.filetypes.append(FileType(extension="mkv"))
        session.add(server)
        session.commit()
        server_id = server.id

    with Session(engine) as session:
        server = session.get(Server, server_id)
        assert server is not None
        session.delete(server)
        session.commit()

    with Session(engine) as session:
        assert list(session.exec(select(Folder)).all()) == []
        assert list(session.exec(select(FileType)).all()) == []


def test_setting_table_round_trips() -> None:
    engine = _memory_engine()
    with Session(engine) as session:
        session.add(Setting(key="schema_version", value="1"))
        session.commit()
    with Session(engine) as session:
        row = session.get(Setting, "schema_version")
        assert row is not None
        assert row.value == "1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.db.models'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/db/models.py` (reproduce the frozen contract verbatim):

```python
"""SQLModel persistence models and enums (frozen interface contract, sections 1-2).

`FileType` is its own table so the `Server >- Folder >- FileType` cascade delete can be
tested explicitly. Secrets live only as Fernet ciphertext in `Server.secret_encrypted`;
plaintext never touches a model field.
"""

from __future__ import annotations

from enum import Enum

from sqlmodel import Field, Relationship, SQLModel


class ServerType(str, Enum):
    webhook = "webhook"
    plex = "plex"
    emby = "emby"
    jellyfin = "jellyfin"
    audiobookshelf = "audiobookshelf"


class ScanMode(str, Enum):
    targeted = "targeted"   # backend scans a specific folder path (Plex ?path=)
    library = "library"     # backend refreshes a whole library id


class DebounceMode(str, Enum):
    off = "off"             # dispatch every matching event
    trailing = "trailing"   # collapse a burst per (server_id, scan_key) after a window


class Server(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    type: ServerType
    base_url: str = ""                       # "" for pure webhook with full URL in template
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    secret_encrypted: str | None = None       # Fernet token; never the plaintext
    scan_mode: ScanMode = ScanMode.targeted
    debounce_mode: DebounceMode = DebounceMode.trailing
    debounce_window_seconds: int = 30
    retry_attempts: int = 3                    # total tries (1 = no retry)
    enabled: bool = True
    # webhook-only (unused until Phase 2, defined now to avoid a Phase 2 migration):
    webhook_method: str | None = None
    webhook_headers_json: str | None = None
    webhook_body_template: str | None = None
    folders: list["Folder"] = Relationship(
        back_populates="server",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Folder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    path: str                                  # host path watched, e.g. /data/media/tvseries
    library_id: str | None = None              # backend section/library id; None for webhook
    enabled: bool = True
    server: Server = Relationship(back_populates="folders")
    filetypes: list["FileType"] = Relationship(
        back_populates="folder",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class FileType(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    folder_id: int = Field(foreign_key="folder.id", index=True)
    extension: str                             # normalized: lowercase, no leading dot
    folder: Folder = Relationship(back_populates="filetypes")


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)         # e.g. "schema_version", "password_hash"
    value: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_models.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/models.py tests/db/__init__.py tests/db/test_models.py
git commit -m "feat(db): add SQLModel tables and enums with cascade delete"
```

---

### Task 2: `db/crypto.py` — Fernet secret box + key loading

**Files:**
- Create: `mediascanmonitor/db/crypto.py`
- Test: `tests/db/test_crypto.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_crypto.py`:

```python
"""Tests for SecretBox / load_or_create_key (contract section 3)."""

from __future__ import annotations

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


def test_load_or_create_key_generates_file_with_mode_0600(tmp_path: Path) -> None:
    key_path = tmp_path / "sub" / "secret.key"
    key = load_or_create_key(key_path)
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    # the generated key must be a usable Fernet key
    box = SecretBox(key)
    assert box.decrypt(box.encrypt("value")) == "value"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.db.crypto'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/db/crypto.py`:

```python
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
    generate a new key, write it to ``path`` with mode 0600, and return it.
    """
    if env_key:
        return env_key.encode("ascii")
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_crypto.py -v`
Expected: PASS — `6 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/crypto.py tests/db/test_crypto.py
git commit -m "feat(db): add Fernet SecretBox and load_or_create_key"
```

---

### Task 3: `db/session.py` — engine, migration, session factory

**Files:**
- Create: `mediascanmonitor/db/session.py`
- Test: `tests/db/test_session.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_session.py`:

```python
"""Tests for init_db / session_factory (contract conventions; schema_version)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, col, select

from mediascanmonitor.db.models import Setting
from mediascanmonitor.db.session import (
    SCHEMA_VERSION,
    init_db,
    resolve_db_path,
    session_factory,
)


def test_init_db_seeds_schema_version(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")
    with Session(engine) as session:
        setting = session.get(Setting, "schema_version")
        assert setting is not None
        assert setting.value == "1"
        assert SCHEMA_VERSION == "1"


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    init_db(db)
    engine = init_db(db)  # second call must not error or duplicate the row
    with Session(engine) as session:
        rows = list(
            session.exec(select(Setting).where(col(Setting.key) == "schema_version")).all()
        )
        assert len(rows) == 1
        assert rows[0].value == "1"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.db.session'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/db/session.py`:

```python
"""SQLite engine, schema migration, and session factory.

`init_db` creates the engine, runs `SQLModel.metadata.create_all`, and seeds the
`schema_version` setting (the explicit migration step required by the project rules).
Sessions are built with `expire_on_commit=False` so ORM instances returned from `Repo`
methods stay usable after their session closes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from mediascanmonitor.db.models import Setting

SCHEMA_VERSION = "1"
DEFAULT_DB_PATH = "/config/app.db"


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
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


def init_db(db_path: str | os.PathLike[str]) -> Engine:
    """Create the engine, build all tables, run migrations. Idempotent."""
    engine = create_db_engine(db_path)
    SQLModel.metadata.create_all(engine)
    _run_migrations(engine)
    return engine


def _run_migrations(engine: Engine) -> None:
    with Session(engine) as session:
        if session.get(Setting, "schema_version") is None:
            session.add(Setting(key="schema_version", value=SCHEMA_VERSION))
            session.commit()


def session_factory(engine: Engine) -> Callable[[], Session]:
    """Return a zero-arg callable producing fresh sessions bound to ``engine``."""

    def factory() -> Session:
        return Session(engine, expire_on_commit=False)

    return factory
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_session.py -v`
Expected: PASS — `4 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/session.py tests/db/test_session.py
git commit -m "feat(db): add init_db migration step and session_factory"
```

---

### Task 4: `db/schemas.py` — Pydantic boundary models

**Files:**
- Create: `mediascanmonitor/db/schemas.py`
- Test: `tests/db/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_schemas.py`:

```python
"""Tests for the Pydantic boundary schemas (contract section 4)."""

from __future__ import annotations

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate


def test_server_create_defaults() -> None:
    s = ServerCreate(name="plex1", type=ServerType.plex)
    assert s.base_url == ""
    assert s.verify_tls is True
    assert s.timeout_seconds == 10.0
    assert s.secret is None
    assert s.scan_mode is ScanMode.targeted
    assert s.debounce_mode is DebounceMode.trailing
    assert s.debounce_window_seconds == 30
    assert s.retry_attempts == 3
    assert s.enabled is True


def test_server_create_accepts_plaintext_secret() -> None:
    s = ServerCreate(name="plex1", type=ServerType.plex, secret="plain")
    assert s.secret == "plain"


def test_server_update_tracks_only_set_fields() -> None:
    u = ServerUpdate(enabled=False)
    assert u.model_dump(exclude_unset=True) == {"enabled": False}

    u2 = ServerUpdate(secret="new", base_url="https://new:32400")
    assert u2.model_dump(exclude_unset=True) == {
        "secret": "new",
        "base_url": "https://new:32400",
    }

    assert ServerUpdate().model_dump(exclude_unset=True) == {}


def test_folder_create_defaults() -> None:
    f = FolderCreate(path="/data/tv")
    assert f.path == "/data/tv"
    assert f.library_id is None
    assert f.extensions == []
    assert f.enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.db.schemas'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/db/schemas.py`:

```python
"""Pydantic boundary models for repo writes (contract section 4).

`secret` is **plaintext-in**: the caller supplies the raw token and the repo encrypts it
before storage. `ServerUpdate` is a partial-update model — callers send only the fields
they want changed, and the repo applies them via ``model_dump(exclude_unset=True)``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType


class ServerCreate(BaseModel):
    name: str
    type: ServerType
    base_url: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    secret: str | None = None      # plaintext; encrypted by the repo
    scan_mode: ScanMode = ScanMode.targeted
    debounce_mode: DebounceMode = DebounceMode.trailing
    debounce_window_seconds: int = 30
    retry_attempts: int = 3
    enabled: bool = True
    webhook_method: str | None = None
    webhook_headers_json: str | None = None
    webhook_body_template: str | None = None


class ServerUpdate(BaseModel):
    name: str | None = None
    type: ServerType | None = None
    base_url: str | None = None
    verify_tls: bool | None = None
    timeout_seconds: float | None = None
    secret: str | None = None      # plaintext; if set, re-encrypted by the repo
    scan_mode: ScanMode | None = None
    debounce_mode: DebounceMode | None = None
    debounce_window_seconds: int | None = None
    retry_attempts: int | None = None
    enabled: bool | None = None
    webhook_method: str | None = None
    webhook_headers_json: str | None = None
    webhook_body_template: str | None = None


class FolderCreate(BaseModel):
    path: str
    library_id: str | None = None
    extensions: list[str] = Field(default_factory=list)
    enabled: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_schemas.py -v`
Expected: PASS — `4 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/schemas.py tests/db/test_schemas.py
git commit -m "feat(db): add ServerCreate/ServerUpdate/FolderCreate boundary schemas"
```

---

### Task 5: `db/repo.py` — the sync repository

**Files:**
- Create: `mediascanmonitor/db/repo.py`
- Create: `tests/db/conftest.py`
- Test: `tests/db/test_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/conftest.py`:

```python
"""Shared fixtures for repo tests: a real file-backed SQLite DB under tmp_path."""

from __future__ import annotations

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
```

Create `tests/db/test_repo.py`:

```python
"""Tests for the Repo CRUD/crypto contract (contract section 4)."""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, select

from mediascanmonitor.db.models import FileType, Folder, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate


def make_server(
    name: str = "plex1", *, enabled: bool = True, secret: str | None = "tok"
) -> ServerCreate:
    return ServerCreate(
        name=name,
        type=ServerType.plex,
        base_url="https://plex:32400",
        secret=secret,
        enabled=enabled,
    )


def test_create_server_encrypts_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret="my-token"))
    assert server.id is not None
    assert server.secret_encrypted is not None
    assert server.secret_encrypted != "my-token"
    assert repo.resolve_secret(server) == "my-token"


def test_create_server_without_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret=None))
    assert server.secret_encrypted is None
    assert repo.resolve_secret(server) is None


def test_get_server_round_trip_and_missing(repo: Repo) -> None:
    created = repo.create_server(make_server())
    assert created.id is not None
    fetched = repo.get_server(created.id)
    assert fetched is not None
    assert fetched.name == "plex1"
    assert repo.get_server(9999) is None


def test_list_servers_enabled_only(repo: Repo) -> None:
    repo.create_server(make_server(name="on", enabled=True))
    repo.create_server(make_server(name="off", enabled=False))
    assert len(repo.list_servers()) == 2
    enabled = repo.list_servers(enabled_only=True)
    assert [s.name for s in enabled] == ["on"]


def test_update_server_changes_fields_and_keeps_secret(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    updated = repo.update_server(
        server.id, ServerUpdate(base_url="https://new:32400", enabled=False)
    )
    assert updated.base_url == "https://new:32400"
    assert updated.enabled is False
    assert repo.resolve_secret(updated) == "tok"  # secret untouched


def test_update_server_reencrypts_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret="old"))
    assert server.id is not None
    old_ciphertext = server.secret_encrypted
    updated = repo.update_server(server.id, ServerUpdate(secret="new"))
    assert updated.secret_encrypted != old_ciphertext
    assert repo.resolve_secret(updated) == "new"


def test_delete_server_cascades_to_folders_and_filetypes(
    repo: Repo, factory: Callable[[], Session]
) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(
        server.id,
        FolderCreate(path="/data/tv", library_id="2", extensions=["mkv", "srt"]),
    )
    repo.delete_server(server.id)
    assert repo.get_server(server.id) is None
    assert repo.list_folders(server.id) == []
    with factory() as session:
        assert list(session.exec(select(Folder)).all()) == []
        assert list(session.exec(select(FileType)).all()) == []


def test_create_folder_normalizes_path_and_extensions(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv/", extensions=[".MKV", " Srt "])
    )
    assert folder.path == "/data/tv"
    assert {ft.extension for ft in folder.filetypes} == {"mkv", "srt"}


def test_list_folders_returns_filetypes(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(server.id, FolderCreate(path="/data/tv", extensions=["mkv"]))
    folders = repo.list_folders(server.id)
    assert len(folders) == 1
    assert {ft.extension for ft in folders[0].filetypes} == {"mkv"}


def test_delete_folder(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(server.id, FolderCreate(path="/data/tv"))
    assert folder.id is not None
    repo.delete_folder(folder.id)
    assert repo.list_folders(server.id) == []


def test_set_filetypes_replaces_wholesale_and_normalizes(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv", extensions=["mkv", "mp4"])
    )
    assert folder.id is not None
    result = repo.set_filetypes(folder.id, [".SRT"])
    assert [ft.extension for ft in result] == ["srt"]
    folders = repo.list_folders(server.id)
    assert {ft.extension for ft in folders[0].filetypes} == {"srt"}


def test_set_filetypes_empty_list_means_all(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv", extensions=["mkv"])
    )
    assert folder.id is not None
    result = repo.set_filetypes(folder.id, [])
    assert result == []
    folders = repo.list_folders(server.id)
    assert folders[0].filetypes == []


def test_settings_get_and_set(repo: Repo) -> None:
    assert repo.get_setting("missing") is None
    repo.set_setting("password_hash", "abc")
    assert repo.get_setting("password_hash") == "abc"
    repo.set_setting("password_hash", "def")  # overwrite
    assert repo.get_setting("password_hash") == "def"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.db.repo'`

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/db/repo.py`:

```python
"""Synchronous repository over the SQLModel tables (contract section 4).

A `SecretBox` is injected so the repo stores Fernet ciphertext and never leaks plaintext
into the DB. Plaintext is returned ONLY by `resolve_secret`. Paths and extensions are
normalized at write time via the `config.defaults` helpers (cross-plan: owned by
sub-plan 02). Sessions come from a `Callable[[], Session]` factory; methods are sync.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, col, select

from mediascanmonitor.config.defaults import normalize_extension, normalize_path
from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.models import FileType, Folder, Server, Setting
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate


class Repo:
    def __init__(self, session_factory: Callable[[], Session], box: SecretBox) -> None:
        self._session_factory = session_factory
        self._box = box

    # servers ----------------------------------------------------------------
    def create_server(self, data: ServerCreate) -> Server:
        with self._session_factory() as session:
            server = Server(
                name=data.name,
                type=data.type,
                base_url=data.base_url,
                verify_tls=data.verify_tls,
                timeout_seconds=data.timeout_seconds,
                secret_encrypted=(
                    self._box.encrypt(data.secret) if data.secret is not None else None
                ),
                scan_mode=data.scan_mode,
                debounce_mode=data.debounce_mode,
                debounce_window_seconds=data.debounce_window_seconds,
                retry_attempts=data.retry_attempts,
                enabled=data.enabled,
                webhook_method=data.webhook_method,
                webhook_headers_json=data.webhook_headers_json,
                webhook_body_template=data.webhook_body_template,
            )
            session.add(server)
            session.commit()
            return server

    def get_server(self, server_id: int) -> Server | None:
        with self._session_factory() as session:
            return session.get(Server, server_id)

    def list_servers(self, *, enabled_only: bool = False) -> list[Server]:
        with self._session_factory() as session:
            statement = select(Server)
            if enabled_only:
                statement = statement.where(col(Server.enabled).is_(True))
            return list(session.exec(statement).all())

    def update_server(self, server_id: int, data: ServerUpdate) -> Server:
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                raise KeyError(f"server {server_id} not found")
            fields = data.model_dump(exclude_unset=True)
            if "secret" in fields:
                secret = fields.pop("secret")
                server.secret_encrypted = (
                    self._box.encrypt(secret) if secret is not None else None
                )
            for key, value in fields.items():
                setattr(server, key, value)
            session.add(server)
            session.commit()
            return server

    def delete_server(self, server_id: int) -> None:
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                return
            session.delete(server)
            session.commit()

    # folders ----------------------------------------------------------------
    def create_folder(self, server_id: int, data: FolderCreate) -> Folder:
        with self._session_factory() as session:
            folder = Folder(
                server_id=server_id,
                path=normalize_path(data.path),
                library_id=data.library_id,
                enabled=data.enabled,
            )
            for ext in data.extensions:
                folder.filetypes.append(FileType(extension=normalize_extension(ext)))
            session.add(folder)
            session.commit()
            return folder

    def list_folders(self, server_id: int) -> list[Folder]:
        with self._session_factory() as session:
            statement = select(Folder).where(col(Folder.server_id) == server_id)
            folders = list(session.exec(statement).all())
            for folder in folders:
                _ = folder.filetypes  # force-load while the session is open
            return folders

    def delete_folder(self, folder_id: int) -> None:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                return
            session.delete(folder)
            session.commit()

    # filetypes --------------------------------------------------------------
    def set_filetypes(self, folder_id: int, extensions: list[str]) -> list[FileType]:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                raise KeyError(f"folder {folder_id} not found")
            for existing in list(folder.filetypes):
                session.delete(existing)
            session.flush()
            new_types = [
                FileType(folder_id=folder_id, extension=normalize_extension(ext))
                for ext in extensions
            ]
            for filetype in new_types:
                session.add(filetype)
            session.commit()
            return new_types

    # secrets / settings -----------------------------------------------------
    def resolve_secret(self, server: Server) -> str | None:
        if server.secret_encrypted is None:
            return None
        return self._box.decrypt(server.secret_encrypted)

    def get_setting(self, key: str) -> str | None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            return setting.value if setting is not None else None

    def set_setting(self, key: str, value: str) -> None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            if setting is None:
                session.add(Setting(key=key, value=value))
            else:
                setting.value = value
                session.add(setting)
            session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_repo.py -v`
Expected: PASS — `13 passed`

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/repo.py tests/db/conftest.py tests/db/test_repo.py
git commit -m "feat(db): add sync Repo with encrypted secrets and cascade delete"
```

---

### Task 6: Full sub-plan verification (ruff + mypy + pytest)

**Files:** (no new files — verification only)

- [ ] **Step 1: Run the linter**

Run: `ruff check mediascanmonitor tests`
Expected: `All checks passed!`

- [ ] **Step 2: Run the formatter check**

Run: `ruff format --check mediascanmonitor tests`
Expected: all files already formatted (no reformat needed). If it reports files would be
reformatted, run `ruff format mediascanmonitor tests`, then re-run the check and amend the
relevant prior commit (or add a follow-up `style:` commit).

- [ ] **Step 3: Run the type checker**

Run: `mypy mediascanmonitor`
Expected: `Success: no issues found in N source files`

- [ ] **Step 4: Run the full DB + config test suites**

Run: `pytest tests/db tests/config -v`
Expected: PASS — all tests green (`5 + 6 + 4 + 4 + 13 + 2 = 34 passed`).

- [ ] **Step 5: Run the entire test suite (no regressions)**

Run: `pytest`
Expected: PASS — the Phase 0 CLI tests plus all new DB/config tests pass.

- [ ] **Step 6: Commit (only if Step 2 required formatting changes)**

```bash
git add mediascanmonitor tests
git commit -m "style: apply ruff format to db/crypto modules"
```

---

## Self-Review

**1. Spec coverage** (each item from the prompt's "Tests you MUST include" and scope):

- `models.py` enums + tables exactly per contract sections 1–2 → Task 1. ✓
- Model creation + relationships → `test_relationships_persist` (Task 1). ✓
- Cascade delete server→folders→filetypes → `test_cascade_delete_removes_folders_and_filetypes`
  (Task 1, model level) **and** `test_delete_server_cascades_to_folders_and_filetypes`
  (Task 5, repo level). ✓
- `SecretBox` encrypt/decrypt round-trip → `test_encrypt_decrypt_round_trip` (Task 2). ✓
- bad token raises `SecretDecryptError` → `test_decrypt_bad_token_raises_secret_decrypt_error`
  + wrong-key variant (Task 2). ✓
- `load_or_create_key` precedence env > file > generate+chmod 0600 → three tests incl.
  `test_load_or_create_key_generates_file_with_mode_0600` (Task 2). ✓
- Repo `create_server` encrypts (`secret_encrypted != plaintext`, not None, round-trips) →
  `test_create_server_encrypts_secret` (Task 5). ✓
- `list_servers(enabled_only=True)` → `test_list_servers_enabled_only` (Task 5). ✓
- `set_filetypes` replaces wholesale + normalizes `".MKV"→"mkv"` →
  `test_set_filetypes_replaces_wholesale_and_normalizes` (Task 5). ✓
- empty filetype list allowed → `test_set_filetypes_empty_list_means_all` (Task 5). ✓
- `get_setting`/`set_setting` → `test_settings_get_and_set` (Task 5). ✓
- `init_db` idempotent + `schema_version == "1"` → `test_init_db_is_idempotent`,
  `test_init_db_seeds_schema_version` (Task 3). ✓
- `schemas.py` ServerCreate/ServerUpdate/FolderCreate with plaintext `secret` → Task 4. ✓
- `config/defaults.py` `normalize_extension`/`normalize_path` stub + cross-plan note → Task 0. ✓
- mypy --strict / ruff / line-length 100 / `from __future__ import annotations` → every module
  has the future import; Task 6 verifies. ✓
- Repo methods sync; secrets only via `resolve_secret` → enforced in `repo.py` (Task 5). ✓
- Commits `git add` only task-relevant files → every Task's Step 5. ✓

**2. Placeholder scan:** No "TODO"/"TBD"/"implement later"/"add validation"/"similar to above".
Every code step contains complete, runnable code. The only "owned by sub-plan 02" markers are the
deliberate, required cross-plan reconciliation notes (Task 0), not deferred work in this plan.

**3. Type consistency:** Names match across tasks and the contract — `SecretBox`,
`SecretDecryptError`, `load_or_create_key`, `init_db`, `session_factory`, `SCHEMA_VERSION`,
`resolve_db_path`, `Repo`, `ServerCreate`/`ServerUpdate`/`FolderCreate`,
`ServerType`/`ScanMode`/`DebounceMode`, `Server`/`Folder`/`FileType`/`Setting`,
`normalize_extension`/`normalize_path`. Repo method signatures (`create_server`, `get_server`,
`list_servers`, `update_server`, `delete_server`, `create_folder`, `list_folders`,
`delete_folder`, `set_filetypes`, `resolve_secret`, `get_setting`, `set_setting`) match contract
section 4 exactly. `col(...)` from `sqlmodel` is used for boolean/column comparisons to stay
`mypy --strict` clean without the SQLAlchemy mypy plugin. No method is referenced before it is
defined.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-17-phase1-01-db-and-crypto.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
