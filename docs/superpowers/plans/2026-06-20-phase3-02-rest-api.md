# Phase 3 — Sub-plan 02: REST API (CRUD + test + rebuild-on-write) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. COMPLETE code is given in every step — paste it, then
> normalize (`ruff format` → `ruff check --fix`) before asserting "clean".

**Goal:** Build the JSON CRUD surface for the dashboard — `/api/servers`, `/api/servers/{id}/folders`,
and `/api/events/recent` — plus the `POST /api/servers/{id}/test` connectivity probe. Every read is
**secret-redacted**; every write runs **validate → off-thread repo write → `rebuild_engine`**; a
server whose type **requires a token** but would be saved without one is rejected with **422** at the
boundary. The folder write path is unlocked by adding `FolderUpdate` + `repo.get_folder` /
`repo.update_folder`.

**Architecture:** Three thin FastAPI routers under `mediascanmonitor/web/api/` mounted by
`create_app` (sub-plan 01). The routers parse/validate input with the existing write-schemas
(`ServerCreate`/`ServerUpdate`/`FolderCreate`/`FolderUpdate`) and never touch the engine or the repo
directly for mutations: they call the **shared write-cores** in `web/writes.py` (contract §K), which
both this sub-plan's `/api/*` routes and sub-plan 04's `/ui/*` HTML routes invoke, so the two
surfaces can never drift on validation or rebuild. Reads go through the redacted read-models in
`web/api_schemas.py` (contract §D), which expose `has_secret: bool` and **never** the token or its
ciphertext. The `test` endpoint builds a throwaway `ServerRuntime` from the stored row (decrypting the
secret in memory only), constructs the registered adapter via `create_adapter`, awaits `adapter.test()`,
and always `aclose()`s its client.

**Tech Stack:** Python 3.14, `fastapi==0.137.1`, `httpx==0.28.1` (Starlette `TestClient`),
`respx==0.23.1` (mock the adapter probe), `pydantic` (boundary models), `pytest==9.1.0` +
`pytest-asyncio==1.4.0` (`asyncio_mode = "auto"` — no decorator needed on async tests). `ruff` +
`mypy --strict` clean, line length 100. PEP 649 annotations — **no** `from __future__ import annotations`.

## Global Constraints

- **PEP 649 / no `from __future__ import annotations`** in any module. Forward refs stay unquoted
  (`-> FolderRead`); a classmethod return annotation to its own enclosing class is fine under PEP 649
  (deferred evaluation) and is **not** runtime-introspected by Pydantic (only field annotations are).
- **Enums are `StrEnum`** (`ServerType`/`ScanMode`/`DebounceMode`). `str(member)` / sorting is by the
  bare value. Never `(str, Enum)`.
- **ruff `select` is exactly `E,F,I,UP,B,C4,SIM,RUF`**; per-file-ignore `B` under `tests/**`. Do not
  add a `# noqa` for an unselected rule (trips `RUF100`). `mediascanmonitor` is **first-party** for
  isort — keep a blank line between the third-party and first-party import groups.
- **FastAPI `Depends`/`Query` in parameter defaults trip ruff `B008`.** Sub-plan 01 (Task 1) added
  bugbear's immutable-call allow-list to `pyproject.toml`, covering every marker this sub-plan uses
  (`fastapi.Depends`, `fastapi.Query`, `fastapi.Form`, `fastapi.Path`, `fastapi.Body`,
  `fastapi.Header`):
  ```toml
  [tool.ruff.lint.flake8-bugbear]
  extend-immutable-calls = ["fastapi.Depends", "fastapi.Query", "fastapi.Form", "fastapi.Path", "fastapi.Body", "fastapi.Header"]
  ```
  This sub-plan **reuses** it — do not add a second `[tool.ruff.lint.flake8-bugbear]` table (TOML
  forbids duplicate headers). If, executing out of order, you find the table missing or missing
  `fastapi.Query` (the first marker this sub-plan introduces, in `api/events.py`), add/extend it
  rather than sprinkling `# noqa: B008`.
- **mypy `--strict`:** full type hints; no untyped `dict` plumbing. `server.id` / `folder.id` are
  `int | None` on the SQLModel rows — `assert ... is not None` before use (persisted rows always carry
  an id).
- **Off-loop I/O (contract invariant 3 / convention 1):** every `Repo` call from an `async def` goes
  through `await asyncio.to_thread(...)`. `EventsBus.recent()` is sync/non-blocking (a `deque` slice)
  and may be called directly. Adapter `test()` is async — `await` it.
- **Secrets redacted on read (contract invariant 1 / CLAUDE.md rule 5):** no read-model, response
  body, URL, or log line ever carries the token or its ciphertext — only `has_secret: bool`. Writes
  take plaintext `secret` (the repo encrypts it).
- **Every write calls `rebuild_engine` (contract invariant 4):** the shared write-cores do this
  **after** the repo commit and **before** the handler returns. A detached/blocked engine never makes
  a write 500 (`rebuild_engine` swallows `RuntimeError`).
- **Token-required 422 (contract §D):** the misconfiguration is caught at the boundary, accounting for
  `ServerUpdate` tri-state (omitted secret keeps the existing one; explicit `None` clears it).
- **No server-type special-casing (rule 2 / contract invariant 6):** per-type rules come **only** from
  `SERVER_TYPE_SPECS` + `registry.get_adapter_class(type).supported_scan_modes`; no router or schema
  branches on a literal type name.
- Verification gate: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`.

## Tooling note (run once per shell)

Project tools live in the venv, not on `PATH`. Run this once at the repo root, then use the bare
commands (`pytest`, `ruff`, `mypy`) shown in each step:

```bash
export PATH="$PWD/.venv/bin:$PATH"
```

## Prerequisites

- **Phase 1 + Phase 2 merged** (engine/repo/adapters/pipeline). Consumed unchanged: `Repo`
  (`db/repo.py`), `Server`/`Folder`/`FileType`/`ServerType`/`ScanMode`/`DebounceMode` (`db/models.py`),
  `ServerCreate`/`ServerUpdate`/`FolderCreate` (`db/schemas.py`), `normalize_extension`/`normalize_path`
  (`normalize.py`), `ServerRuntime` (`config/runtime.py`), `ServerAdapter` (`servers/base.py`),
  `get_adapter_class`/`create_adapter` (`servers/registry.py`), `build_client` (`servers/http.py`),
  `Engine`/`Engine.rebuild` (`engine.py`). The `servers` package self-registers every adapter on import
  (`servers/__init__.py`), so `from mediascanmonitor.servers import registry` makes
  `get_adapter_class(ServerType.<any>)` resolvable.
- **Phase 3 sub-plan 01 merged** (the FastAPI foundation). This sub-plan **assumes**:
  - `mediascanmonitor/web/app.py` → `create_app(repo, engine, events_bus, *, session_secret) -> FastAPI`
    (contract §A) — mounts middleware/routers; this plan adds three `include_router` lines to it.
  - `mediascanmonitor/web/deps.py` → `get_repo`/`get_engine`/`get_events_bus` (state accessors) and
    `require_api_auth` (raises `HTTPException(401)` when not authed; `401 "setup required"` when no
    password set) — contract §B.
  - `mediascanmonitor/observ/events_bus.py` → `EventsBus` + `EventRecord` (contract §G).
  - `tests/web/conftest.py` fixtures (created by 01): **`repo`** (real `Repo` on a tmp SQLite DB),
    **`events_bus`** (`EventsBus()`), **`engine`** (a `FakeEngine` stub exposing `.state`,
    `.watch_limit`, and an async `rebuild()` that increments `.rebuild_calls`), **`app`**
    (`create_app(repo, engine, events_bus, session_secret=...)` over those same three fixtures),
    **`client`** (unauthenticated `TestClient`), **`auth_client`** (a `TestClient` whose session is
    already logged in), and **`aclient`** (async httpx client for SSE — unused here).

This sub-plan depends only on 01 and is independent of 03 (per the README dependency graph). The one
shared file is `web/app.py` (append-only `include_router` lines — trivial merge).

## File Structure (what this plan builds)

| File | Action | Responsibility |
|------|--------|----------------|
| `mediascanmonitor/db/schemas.py` | Modify | Add `FolderUpdate` (path/extensions validators mirror `FolderCreate`, tri-state Optionals). |
| `mediascanmonitor/db/repo.py` | Modify | Add `get_folder` (filetypes force-loaded) + `update_folder` (`exclude_unset` tri-state; replace `FileType` rows when `extensions` present). |
| `tests/db/test_folder_update.py` | Create | Repo unit tests for `FolderUpdate` validators + `get_folder` / `update_folder`. |
| `mediascanmonitor/web/api_schemas.py` | Create | `FolderRead`/`ServerRead`/`ServerTestResponse`/`EventRead` read-models + `ServerTypeSpec` + `SERVER_TYPE_SPECS`. |
| `tests/web/test_api_schemas.py` | Create | Redaction + `supported_scan_modes` + specs unit tests. |
| `mediascanmonitor/web/rebuild.py` | Create | `rebuild_engine(engine)` — `await engine.rebuild()`, swallow `RuntimeError`, log `web.rebuild_skipped`. |
| `tests/web/test_rebuild.py` | Create | `rebuild_engine` success + `RuntimeError`-tolerant unit tests. |
| `mediascanmonitor/web/writes.py` | Create | Shared write-cores: `apply_server_{create,update,delete}` + `apply_folder_{create,update,delete}` (token-required 422, off-thread write, rebuild). |
| `tests/web/test_writes.py` | Create | 422 paths + rebuild-called unit tests. |
| `mediascanmonitor/web/api/servers.py` | Create | `/api/servers` CRUD + `POST /{id}/test`. |
| `tests/web/test_api_servers.py` | Create | CRUD happy paths, redaction, 422, 404s, auth-401, `test` (respx). |
| `mediascanmonitor/web/api/folders.py` | Create | `/api/servers/{id}/folders` CRUD. |
| `tests/web/test_api_folders.py` | Create | Folder CRUD, 404s, auth-401. |
| `mediascanmonitor/web/api/events.py` | Create | `GET /api/events/recent`. |
| `tests/web/test_api_events.py` | Create | recent-events read tests. |
| `mediascanmonitor/web/app.py` | Modify | `include_router(servers_router/folders_router/events_router)`. |

---

### Task 1: `FolderUpdate` + `repo.get_folder` / `repo.update_folder`

**Files:**
- Create: `tests/db/test_folder_update.py`
- Modify: `mediascanmonitor/db/schemas.py`
- Modify: `mediascanmonitor/db/repo.py`

**Interfaces:**
- Consumes: `normalize_extension`/`normalize_path` (`normalize.py`); `Folder`/`FileType` (`db/models.py`);
  the `repo` fixture (`tests/db/conftest.py`).
- Produces:
  ```python
  # db/schemas.py
  class FolderUpdate(BaseModel):
      path: str | None = None             # validator: if provided, normalize + require absolute
      library_id: str | None = None
      extensions: list[str] | None = None # validator: if provided, normalize/drop-empty/dedupe
      enabled: bool | None = None

  # db/repo.py (Repo)
  def get_folder(self, folder_id: int) -> Folder | None: ...
  def update_folder(self, folder_id: int, data: FolderUpdate) -> Folder: ...
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_folder_update.py`:

```python
"""FolderUpdate validators + repo.get_folder / repo.update_folder (contract §E)."""

import pytest

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate
from mediascanmonitor.db.models import ServerType


def _make_folder(repo: Repo) -> int:
    server = repo.create_server(ServerCreate(name="plex", type=ServerType.plex, secret="t"))
    assert server.id is not None
    folder = repo.create_folder(
        server.id,
        FolderCreate(path="/data/tv", library_id="2", extensions=["MKV", ".mp4"]),
    )
    assert folder.id is not None
    return folder.id


def test_folder_update_normalizes_path() -> None:
    data = FolderUpdate(path="/data/tv/../movies/")
    assert data.path == "/data/movies"


def test_folder_update_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        FolderUpdate(path="relative/dir")


def test_folder_update_normalizes_and_dedupes_extensions() -> None:
    data = FolderUpdate(extensions=[".MKV", "mkv", "", " mp4 "])
    assert data.extensions == ["mkv", "mp4"]


def test_folder_update_unset_fields_excluded() -> None:
    dumped = FolderUpdate(enabled=False).model_dump(exclude_unset=True)
    assert dumped == {"enabled": False}


def test_get_folder_returns_none_for_missing(repo: Repo) -> None:
    assert repo.get_folder(999) is None


def test_get_folder_loads_filetypes(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    folder = repo.get_folder(folder_id)
    assert folder is not None
    assert sorted(ft.extension for ft in folder.filetypes) == ["mkv", "mp4"]


def test_update_folder_missing_raises_keyerror(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.update_folder(999, FolderUpdate(enabled=False))


def test_update_folder_partial_leaves_other_fields(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(enabled=False))
    assert updated.enabled is False
    assert updated.path == "/data/tv"
    assert updated.library_id == "2"
    assert sorted(ft.extension for ft in updated.filetypes) == ["mkv", "mp4"]


def test_update_folder_replaces_extensions(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(extensions=[".AVI", "avi", "flac"]))
    assert sorted(ft.extension for ft in updated.filetypes) == ["avi", "flac"]


def test_update_folder_empty_extensions_clears(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(extensions=[]))
    assert list(updated.filetypes) == []


def test_update_folder_omitted_extensions_unchanged(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(library_id="9"))
    assert updated.library_id == "9"
    assert sorted(ft.extension for ft in updated.filetypes) == ["mkv", "mp4"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/db/test_folder_update.py -v`
Expected: FAIL — `ImportError: cannot import name 'FolderUpdate'`.

- [ ] **Step 3: Add `FolderUpdate` to `db/schemas.py`**

Append to `mediascanmonitor/db/schemas.py` (below `FolderCreate`):

```python
class FolderUpdate(BaseModel):
    path: str | None = None
    library_id: str | None = None
    extensions: list[str] | None = None
    enabled: bool | None = None

    @field_validator("path")
    @classmethod
    def _normalize_and_require_absolute(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_path(value)
        if not os.path.isabs(normalized):
            raise ValueError(f"folder path must be absolute, got {value!r}")
        return normalized

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, value: list[str] | None) -> list[str] | None:
        # Same rule as FolderCreate: normalize, drop empties, dedupe (order-preserving).
        # None == "field omitted / leave unchanged"; [] == "clear all filetypes".
        if value is None:
            return None
        out: list[str] = []
        for ext in value:
            norm = normalize_extension(ext)
            if norm and norm not in out:
                out.append(norm)
        return out
```

- [ ] **Step 4: Add `get_folder` + `update_folder` to `db/repo.py`**

Update the schema import line in `mediascanmonitor/db/repo.py`:

```python
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
```

Add both methods to the `# folders` section of `Repo` (e.g. just after `list_folders`):

```python
    def get_folder(self, folder_id: int) -> Folder | None:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is not None:
                _ = folder.filetypes  # force-load while the session is open
            return folder

    def update_folder(self, folder_id: int, data: FolderUpdate) -> Folder:
        # exclude_unset tri-state mirrors update_server: an omitted field is left unchanged;
        # extensions present (a list, incl. []) replaces all FileType rows; explicit None is a no-op.
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                raise KeyError(f"folder {folder_id} not found")
            fields = data.model_dump(exclude_unset=True)
            new_exts = fields.pop("extensions", None)
            for key, value in fields.items():
                setattr(folder, key, value)
            if new_exts is not None:
                # delete-orphan cascade deletes the removed rows AND empties the in-memory
                # collection. Do NOT session.delete() each child and then append into the same
                # collection — the cascade re-touches the just-deleted instances and SQLAlchemy
                # raises InvalidRequestError ("Instance has been deleted"). clear() is the safe idiom.
                folder.filetypes.clear()
                # same normalize rule as set_filetypes (raw list[str]).
                normalized: list[str] = []
                for ext in new_exts:
                    norm = normalize_extension(ext)
                    if norm and norm not in normalized:
                        normalized.append(norm)
                for ext in normalized:
                    folder.filetypes.append(FileType(extension=ext))  # folder_id set by the relationship
            session.add(folder)
            session.commit()
            _ = folder.filetypes  # force-load while the session is open
            return folder
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/db/test_folder_update.py -v`
Expected: PASS — 11 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/db/schemas.py mediascanmonitor/db/repo.py tests/db/test_folder_update.py && mypy mediascanmonitor/db/schemas.py mediascanmonitor/db/repo.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/db/schemas.py mediascanmonitor/db/repo.py tests/db/test_folder_update.py
git commit -m "feat(db): add FolderUpdate + repo.get_folder/update_folder (contract §E)"
```

---

### Task 2: Redacted read-models + server-type specs (`web/api_schemas.py`)

**Files:**
- Create: `tests/web/test_api_schemas.py`
- Create: `mediascanmonitor/web/api_schemas.py`

**Interfaces:**
- Consumes: `Folder`/`Server`/`ServerType`/`ScanMode`/`DebounceMode` (`db/models.py`);
  `get_adapter_class` (`servers/registry.py`); `EventRecord` (`observ/events_bus.py`).
- Produces: `FolderRead.from_model`, `ServerRead.from_model`, `ServerTestResponse`, `EventRead.from_record`,
  `ServerTypeSpec`, `SERVER_TYPE_SPECS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_schemas.py`:

```python
"""Redacted read-models + per-type specs (contract §D)."""

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate
from mediascanmonitor.web.api_schemas import (
    SERVER_TYPE_SPECS,
    FolderRead,
    ServerRead,
)


def _seed(repo: Repo) -> tuple[int, int]:
    server = repo.create_server(
        ServerCreate(name="plex", type=ServerType.plex, base_url="http://p:32400", secret="tok")
    )
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv", library_id="2", extensions=["mkv", "mp4"])
    )
    assert folder.id is not None
    return server.id, folder.id


def test_folder_read_sorts_extensions(repo: Repo) -> None:
    server = repo.create_server(ServerCreate(name="p", type=ServerType.plex, secret="t"))
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv", extensions=["mp4", "avi", "mkv"])
    )
    read = FolderRead.from_model(repo.get_folder(folder.id))  # type: ignore[arg-type]
    assert read.extensions == ["avi", "mkv", "mp4"]


def test_server_read_redacts_secret(repo: Repo) -> None:
    server_id, _ = _seed(repo)
    server = repo.get_server(server_id)
    assert server is not None
    read = ServerRead.from_model(server, repo.list_folders(server_id))
    dumped = read.model_dump()
    assert "secret" not in dumped
    assert "secret_encrypted" not in dumped
    assert read.has_secret is True
    assert "tok" not in str(dumped)
    assert server.secret_encrypted is not None
    assert server.secret_encrypted not in str(dumped)


def test_server_read_has_secret_false_when_unset(repo: Repo) -> None:
    server = repo.create_server(ServerCreate(name="hook", type=ServerType.webhook))
    assert server.id is not None
    read = ServerRead.from_model(server, [])
    assert read.has_secret is False


def test_server_read_supported_scan_modes_from_registry(repo: Repo) -> None:
    server = repo.create_server(ServerCreate(name="emby", type=ServerType.emby, secret="t"))
    assert server.id is not None
    read = ServerRead.from_model(server, [])
    # emby only supports library mode (see tests/servers/test_emby.py).
    assert read.supported_scan_modes == [ScanMode.library]


def test_server_read_includes_folders(repo: Repo) -> None:
    server_id, folder_id = _seed(repo)
    server = repo.get_server(server_id)
    assert server is not None
    read = ServerRead.from_model(server, repo.list_folders(server_id))
    assert [f.id for f in read.folders] == [folder_id]


def test_server_type_specs_cover_every_type() -> None:
    assert set(SERVER_TYPE_SPECS) == set(ServerType)
    assert SERVER_TYPE_SPECS[ServerType.webhook].requires_secret is False
    assert SERVER_TYPE_SPECS[ServerType.webhook].is_webhook is True
    assert SERVER_TYPE_SPECS[ServerType.plex].requires_secret is True
    assert SERVER_TYPE_SPECS[ServerType.plex].requires_base_url is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_api_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.api_schemas'`.

- [ ] **Step 3: Implement `web/api_schemas.py`**

Create `mediascanmonitor/web/api_schemas.py`:

```python
"""Redacted API read-models + per-server-type field specs (contract §D).

INVARIANT (CLAUDE.md rule 5 / contract invariant 1): no read-model here ever carries the
secret or its ciphertext — the only secret signal is ``has_secret: bool``. Writes reuse the
plaintext-in write-schemas (ServerCreate/ServerUpdate/FolderCreate/FolderUpdate); reads redact.

SERVER_TYPE_SPECS is the ONE place per-type rules live (rule 2): routers/templates read it +
``registry.get_adapter_class(type).supported_scan_modes`` and never branch on a literal type name.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from mediascanmonitor.db.models import DebounceMode, Folder, ScanMode, Server, ServerType
from mediascanmonitor.observ.events_bus import EventRecord
from mediascanmonitor.servers.registry import get_adapter_class


class FolderRead(BaseModel):
    id: int
    server_id: int
    path: str
    library_id: str | None
    enabled: bool
    extensions: list[str]  # sorted normalized extensions

    @classmethod
    def from_model(cls, folder: Folder) -> FolderRead:
        assert folder.id is not None
        return cls(
            id=folder.id,
            server_id=folder.server_id,
            path=folder.path,
            library_id=folder.library_id,
            enabled=folder.enabled,
            extensions=sorted(ft.extension for ft in folder.filetypes),
        )


class ServerRead(BaseModel):
    id: int
    name: str
    type: ServerType
    base_url: str
    verify_tls: bool
    timeout_seconds: float
    has_secret: bool  # server.secret_encrypted is not None — NEVER the token/ciphertext
    scan_mode: ScanMode
    debounce_mode: DebounceMode
    debounce_window_seconds: int
    retry_attempts: int
    enabled: bool
    supported_scan_modes: list[ScanMode]
    webhook_method: str | None
    webhook_headers_json: str | None
    webhook_body_template: str | None
    folders: list[FolderRead]

    @classmethod
    def from_model(cls, server: Server, folders: list[Folder]) -> ServerRead:
        assert server.id is not None
        return cls(
            id=server.id,
            name=server.name,
            type=server.type,
            base_url=server.base_url,
            verify_tls=server.verify_tls,
            timeout_seconds=server.timeout_seconds,
            has_secret=server.secret_encrypted is not None,
            scan_mode=server.scan_mode,
            debounce_mode=server.debounce_mode,
            debounce_window_seconds=server.debounce_window_seconds,
            retry_attempts=server.retry_attempts,
            enabled=server.enabled,
            supported_scan_modes=sorted(get_adapter_class(server.type).supported_scan_modes),
            webhook_method=server.webhook_method,
            webhook_headers_json=server.webhook_headers_json,
            webhook_body_template=server.webhook_body_template,
            folders=[FolderRead.from_model(f) for f in folders],
        )


class ServerTestResponse(BaseModel):
    ok: bool
    detail: str


class EventRead(BaseModel):
    ts: str
    server_id: int
    server_name: str
    scan_mode: str
    scan_key: str
    scan_path: str | None
    library_id: str | None
    event_type: str
    file_path: str
    ok: bool
    status_code: int | None
    detail: str

    @classmethod
    def from_record(cls, record: EventRecord) -> EventRead:
        return cls(
            ts=record.ts,
            server_id=record.server_id,
            server_name=record.server_name,
            scan_mode=record.scan_mode,
            scan_key=record.scan_key,
            scan_path=record.scan_path,
            library_id=record.library_id,
            event_type=record.event_type,
            file_path=record.file_path,
            ok=record.ok,
            status_code=record.status_code,
            detail=record.detail,
        )


@dataclass(frozen=True, slots=True)
class ServerTypeSpec:
    requires_secret: bool  # a token is mandatory at save time
    requires_base_url: bool  # base_url must be non-empty at save time
    is_webhook: bool  # exposes the webhook_* template fields


SERVER_TYPE_SPECS: dict[ServerType, ServerTypeSpec] = {
    ServerType.plex: ServerTypeSpec(requires_secret=True, requires_base_url=True, is_webhook=False),
    ServerType.emby: ServerTypeSpec(requires_secret=True, requires_base_url=True, is_webhook=False),
    ServerType.jellyfin: ServerTypeSpec(
        requires_secret=True, requires_base_url=True, is_webhook=False
    ),
    ServerType.audiobookshelf: ServerTypeSpec(
        requires_secret=True, requires_base_url=True, is_webhook=False
    ),
    ServerType.webhook: ServerTypeSpec(
        requires_secret=False, requires_base_url=False, is_webhook=True
    ),
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/web/test_api_schemas.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/web/api_schemas.py tests/web/test_api_schemas.py && mypy mediascanmonitor/web/api_schemas.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/api_schemas.py tests/web/test_api_schemas.py
git commit -m "feat(web): redacted read-models + SERVER_TYPE_SPECS (contract §D)"
```

---

### Task 3: Rebuild-on-write helper (`web/rebuild.py`)

**Files:**
- Create: `tests/web/test_rebuild.py`
- Create: `mediascanmonitor/web/rebuild.py`

**Interfaces:**
- Consumes: `Engine` (`engine.py`); `structlog`.
- Produces: `async def rebuild_engine(engine: Engine) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_rebuild.py`:

```python
"""rebuild_engine: calls engine.rebuild(), tolerant of RuntimeError (contract §F)."""

from typing import cast

from mediascanmonitor.engine import Engine
from mediascanmonitor.web.rebuild import rebuild_engine


class _OkEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def rebuild(self) -> None:
        self.calls += 1


class _RaisingEngine:
    async def rebuild(self) -> None:
        raise RuntimeError("Engine.rebuild() called before start()")


async def test_rebuild_engine_calls_rebuild() -> None:
    engine = _OkEngine()
    await rebuild_engine(cast(Engine, engine))
    assert engine.calls == 1


async def test_rebuild_engine_swallows_runtimeerror() -> None:
    # Must not raise: a write while the engine is detached/blocked never 500s.
    await rebuild_engine(cast(Engine, _RaisingEngine()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_rebuild.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.rebuild'`.

- [ ] **Step 3: Implement `web/rebuild.py`**

Create `mediascanmonitor/web/rebuild.py`:

```python
"""Rebuild-on-write helper (contract §F).

Every successful config mutation calls this so the running engine picks up the change with no
restart. TOLERANT: an engine that has not started (or is mid-teardown) raises RuntimeError from
rebuild(); we log and no-op so a write never 500s because the watcher is detached. After sub-plan
03's gate-recovery lands, rebuild() itself handles the blocked state internally; this guard stays
as defense-in-depth.
"""

import structlog

from mediascanmonitor.engine import Engine

log = structlog.get_logger("web")


async def rebuild_engine(engine: Engine) -> None:
    try:
        await engine.rebuild()
    except RuntimeError:
        log.info("web.rebuild_skipped")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/web/test_rebuild.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/web/rebuild.py tests/web/test_rebuild.py && mypy mediascanmonitor/web/rebuild.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/rebuild.py tests/web/test_rebuild.py
git commit -m "feat(web): rebuild-on-write helper tolerant of detached engine (contract §F)"
```

---

### Task 4: Shared write-cores (`web/writes.py`)

**Files:**
- Create: `tests/web/test_writes.py`
- Create: `mediascanmonitor/web/writes.py`

**Interfaces:**
- Consumes: `HTTPException`/`status` (`fastapi`); `Folder`/`Server` (`db/models.py`);
  `Repo` (`db/repo.py`); `ServerCreate`/`ServerUpdate`/`FolderCreate`/`FolderUpdate` (`db/schemas.py`);
  `Engine` (`engine.py`); `SERVER_TYPE_SPECS` (`web/api_schemas.py`); `rebuild_engine` (`web/rebuild.py`).
- Produces (all `async`):
  ```python
  async def apply_server_create(repo: Repo, engine: Engine, data: ServerCreate) -> Server: ...
  async def apply_server_update(repo: Repo, engine: Engine, server_id: int, data: ServerUpdate) -> Server: ...
  async def apply_server_delete(repo: Repo, engine: Engine, server_id: int) -> None: ...
  async def apply_folder_create(repo: Repo, engine: Engine, server_id: int, data: FolderCreate) -> Folder: ...
  async def apply_folder_update(repo: Repo, engine: Engine, folder_id: int, data: FolderUpdate) -> Folder: ...
  async def apply_folder_delete(repo: Repo, engine: Engine, folder_id: int) -> None: ...
  ```
  `apply_server_update` raises `KeyError` when the server is missing (routes translate to 404); the
  token-required check raises `HTTPException(422)`.

- [ ] **Step 1: Write the failing tests**

These use the `repo` + `engine` (FakeEngine, `.rebuild_calls`) fixtures from `tests/web/conftest.py`.

Create `tests/web/test_writes.py`:

```python
"""Shared write-cores: token-required 422 + rebuild-on-write (contract §K/§D)."""

import pytest
from fastapi import HTTPException

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_server_create,
    apply_server_delete,
    apply_server_update,
)


async def test_create_rejects_missing_secret_for_auth_type(repo: Repo, engine: Engine) -> None:
    with pytest.raises(HTTPException) as exc:
        await apply_server_create(repo, engine, ServerCreate(name="plex", type=ServerType.plex))
    assert exc.value.status_code == 422


async def test_create_webhook_without_secret_is_allowed(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="hook", type=ServerType.webhook)
    )
    assert server.id is not None
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_create_calls_rebuild(repo: Repo, engine: Engine) -> None:
    await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_update_missing_secret_clear_rejected(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert server.id is not None
    with pytest.raises(HTTPException) as exc:
        await apply_server_update(repo, engine, server.id, ServerUpdate(secret=None))
    assert exc.value.status_code == 422


async def test_update_omitted_secret_keeps_existing(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert server.id is not None
    updated = await apply_server_update(repo, engine, server.id, ServerUpdate(enabled=False))
    assert updated.enabled is False  # no 422: existing secret still present


async def test_update_missing_server_raises_keyerror(repo: Repo, engine: Engine) -> None:
    with pytest.raises(KeyError):
        await apply_server_update(repo, engine, 999, ServerUpdate(enabled=False))


async def test_delete_calls_rebuild_even_when_absent(repo: Repo, engine: Engine) -> None:
    await apply_server_delete(repo, engine, 999)  # idempotent delete
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_folder_create_calls_rebuild(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="hook", type=ServerType.webhook)
    )
    assert server.id is not None
    before = engine.rebuild_calls  # type: ignore[attr-defined]
    folder = await apply_folder_create(repo, engine, server.id, FolderCreate(path="/data/tv"))
    assert folder.id is not None
    assert engine.rebuild_calls == before + 1  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_writes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.writes'`.

- [ ] **Step 3: Implement `web/writes.py`**

Create `mediascanmonitor/web/writes.py`:

```python
"""Shared validate→write→rebuild cores for server/folder mutations (contract §K).

Both the JSON ``/api/*`` routes (sub-plan 02) and the HTML ``/ui/*`` routes (sub-plan 04) call
these, so the two surfaces can never drift on the §D token-required check or the §F rebuild. Each
core does: token-required validation (servers) → off-thread Repo write (asyncio.to_thread, the repo
is sync SQLModel) → rebuild_engine. Folders carry no secret, so they skip the token check.
"""

import asyncio

from fastapi import HTTPException, status

from mediascanmonitor.db.models import Folder, Server, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS
from mediascanmonitor.web.rebuild import rebuild_engine


def _require_secret_or_422(server_type: ServerType, has_secret: bool) -> None:
    if SERVER_TYPE_SPECS[server_type].requires_secret and not has_secret:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"server type {server_type.value!r} requires a secret/token",
        )


async def apply_server_create(repo: Repo, engine: Engine, data: ServerCreate) -> Server:
    _require_secret_or_422(data.type, data.secret is not None and data.secret != "")
    server = await asyncio.to_thread(repo.create_server, data)
    await rebuild_engine(engine)
    return server


async def apply_server_update(
    repo: Repo, engine: Engine, server_id: int, data: ServerUpdate
) -> Server:
    existing = await asyncio.to_thread(repo.get_server, server_id)
    if existing is None:
        raise KeyError(f"server {server_id} not found")
    dumped = data.model_dump(exclude_unset=True)
    resulting_type = data.type if data.type is not None else existing.type
    if "secret" in dumped:
        # tri-state: explicit value (incl. None) decides; None/"" clears.
        resulting_has_secret = bool(dumped["secret"])
    else:
        resulting_has_secret = existing.secret_encrypted is not None
    _require_secret_or_422(resulting_type, resulting_has_secret)
    server = await asyncio.to_thread(repo.update_server, server_id, data)
    await rebuild_engine(engine)
    return server


async def apply_server_delete(repo: Repo, engine: Engine, server_id: int) -> None:
    await asyncio.to_thread(repo.delete_server, server_id)
    await rebuild_engine(engine)


async def apply_folder_create(
    repo: Repo, engine: Engine, server_id: int, data: FolderCreate
) -> Folder:
    created = await asyncio.to_thread(repo.create_folder, server_id, data)
    await rebuild_engine(engine)
    # repo.create_folder only force-loads `filetypes` when extensions were appended; for an
    # extension-less folder the relationship is unloaded on the committed/detached row, so
    # FolderRead.from_model(...) iterating it would raise DetachedInstanceError. Re-read via
    # get_folder (which force-loads filetypes) so every caller gets a fully-loaded folder.
    assert created.id is not None  # committed rows always carry an id
    folder = await asyncio.to_thread(repo.get_folder, created.id)
    assert folder is not None
    return folder


async def apply_folder_update(
    repo: Repo, engine: Engine, folder_id: int, data: FolderUpdate
) -> Folder:
    folder = await asyncio.to_thread(repo.update_folder, folder_id, data)
    await rebuild_engine(engine)
    return folder


async def apply_folder_delete(repo: Repo, engine: Engine, folder_id: int) -> None:
    await asyncio.to_thread(repo.delete_folder, folder_id)
    await rebuild_engine(engine)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/web/test_writes.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/web/writes.py tests/web/test_writes.py && mypy mediascanmonitor/web/writes.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/writes.py tests/web/test_writes.py
git commit -m "feat(web): shared write-cores with token-required 422 + rebuild (contract §K)"
```

---

### Task 5: Servers router (`web/api/servers.py`) + mount

**Files:**
- Create: `tests/web/test_api_servers.py`
- Create: `mediascanmonitor/web/api/servers.py`
- Modify: `mediascanmonitor/web/app.py`

**Interfaces:**
- Consumes: `APIRouter`/`Depends`/`HTTPException`/`status` (`fastapi`); `ServerRuntime` (`config/runtime.py`);
  `Repo` (`db/repo.py`); `ServerCreate`/`ServerUpdate` (`db/schemas.py`); `Engine` (`engine.py`);
  `build_client` (`servers/http.py`); `create_adapter` (`servers/registry.py`); `ServerRead`/`ServerTestResponse`
  (`web/api_schemas.py`); `get_repo`/`get_engine`/`require_api_auth` (`web/deps.py`); `apply_server_*`
  (`web/writes.py`).
- Produces: `router` (prefix `/api/servers`, guarded by `require_api_auth`).

- [ ] **Step 1: Write the failing tests**

> Uses `auth_client` (logged-in `TestClient`), `client` (unauth), `repo`, and the `respx` mock for the
> `test` endpoint. `ScanMode.targeted` is Plex's default. `argon2`/session handling is sub-plan 01's;
> here we only exercise the JSON surface.

Create `tests/web/test_api_servers.py`:

```python
"""/api/servers CRUD + test endpoint (contract §D)."""

import httpx
import respx
from starlette.testclient import TestClient

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/servers").status_code == 401


def test_list_empty(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/servers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_then_get_redacts_secret(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/api/servers",
        json={"name": "plex", "type": "plex", "base_url": "http://p:32400", "secret": "tok"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_secret"] is True
    assert "secret" not in body
    assert "tok" not in resp.text
    server_id = body["id"]

    got = auth_client.get(f"/api/servers/{server_id}")
    assert got.status_code == 200
    # PlexAdapter.supported_scan_modes is frozenset({targeted, library}); from_model sorts by value,
    # so "library" < "targeted". (This is the *supported* set, distinct from the server's default
    # scan_mode of "targeted".)
    assert got.json()["supported_scan_modes"] == ["library", "targeted"]


def test_create_auth_type_without_secret_is_422(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/servers", json={"name": "plex2", "type": "plex"})
    assert resp.status_code == 422


def test_create_webhook_without_secret_ok(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/servers", json={"name": "hook", "type": "webhook"})
    assert resp.status_code == 201
    assert resp.json()["has_secret"] is False


def test_patch_disables_server(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/servers", json={"name": "emby", "type": "emby", "secret": "t"}
    ).json()
    resp = auth_client.patch(f"/api/servers/{created['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_patch_clearing_secret_is_422(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/servers", json={"name": "emby2", "type": "emby", "secret": "t"}
    ).json()
    resp = auth_client.patch(f"/api/servers/{created['id']}", json={"secret": None})
    assert resp.status_code == 422


def test_get_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/servers/999").status_code == 404


def test_patch_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.patch("/api/servers/999", json={"enabled": False}).status_code == 404


def test_delete_server(auth_client: TestClient) -> None:
    created = auth_client.post("/api/servers", json={"name": "hook2", "type": "webhook"}).json()
    assert auth_client.delete(f"/api/servers/{created['id']}").status_code == 204
    assert auth_client.get(f"/api/servers/{created['id']}").status_code == 404


@respx.mock
def test_test_endpoint_reports_reachable(auth_client: TestClient, repo: Repo) -> None:
    # Emby's test() GETs {base}/System/Info with the token header (tests/servers/test_emby.py).
    server = repo.create_server(
        ServerCreate(
            name="emby-probe", type=ServerType.emby, base_url="http://emby:8096", secret="t"
        )
    )
    assert server.id is not None
    route = respx.get("http://emby:8096/System/Info").mock(return_value=httpx.Response(200))
    resp = auth_client.post(f"/api/servers/{server.id}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert route.called


@respx.mock
def test_test_endpoint_reports_failure(auth_client: TestClient, repo: Repo) -> None:
    server = repo.create_server(
        ServerCreate(
            name="emby-bad", type=ServerType.emby, base_url="http://emby:8096", secret="t"
        )
    )
    assert server.id is not None
    respx.get("http://emby:8096/System/Info").mock(return_value=httpx.Response(401))
    resp = auth_client.post(f"/api/servers/{server.id}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_test_endpoint_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.post("/api/servers/999/test").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_api_servers.py -v`
Expected: FAIL — collection/import error (`web.api.servers` does not exist) or 404 on every route.

- [ ] **Step 3: Implement `web/api/servers.py`**

Create `mediascanmonitor/web/api/servers.py`:

```python
"""/api/servers JSON CRUD + connectivity test (contract §D).

Reads go through the redacted ServerRead; writes through the shared write-cores (web/writes.py),
so the token-required 422 + rebuild-on-write live in exactly one place. The test endpoint builds a
throwaway ServerRuntime from the stored row (secret decrypted in memory only via resolve_secret),
constructs the registered adapter, awaits adapter.test(), and ALWAYS closes its client.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.servers.http import build_client
from mediascanmonitor.servers.registry import create_adapter
from mediascanmonitor.web.api_schemas import ServerRead, ServerTestResponse
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.writes import (
    apply_server_create,
    apply_server_delete,
    apply_server_update,
)

router = APIRouter(
    prefix="/api/servers",
    tags=["servers"],
    dependencies=[Depends(require_api_auth)],
)


async def _read_server(repo: Repo, server_id: int) -> ServerRead:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return ServerRead.from_model(server, folders)


@router.get("")
async def list_servers(repo: Repo = Depends(get_repo)) -> list[ServerRead]:
    servers = await asyncio.to_thread(repo.list_servers)
    out: list[ServerRead] = []
    for server in servers:
        assert server.id is not None
        folders = await asyncio.to_thread(repo.list_folders, server.id)
        out.append(ServerRead.from_model(server, folders))
    return out


@router.get("/{server_id}")
async def get_server(server_id: int, repo: Repo = Depends(get_repo)) -> ServerRead:
    return await _read_server(repo, server_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(
    data: ServerCreate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> ServerRead:
    server = await apply_server_create(repo, engine, data)
    assert server.id is not None
    return await _read_server(repo, server.id)


@router.patch("/{server_id}")
async def update_server(
    server_id: int,
    data: ServerUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> ServerRead:
    try:
        await apply_server_update(repo, engine, server_id, data)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found") from None
    return await _read_server(repo, server_id)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> None:
    await apply_server_delete(repo, engine, server_id)


@router.post("/{server_id}/test")
async def test_server(server_id: int, repo: Repo = Depends(get_repo)) -> ServerTestResponse:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    assert server.id is not None
    secret = await asyncio.to_thread(repo.resolve_secret, server)
    runtime = ServerRuntime(
        server_id=server.id,
        name=server.name,
        type=server.type,
        base_url=server.base_url,
        verify_tls=server.verify_tls,
        timeout_seconds=server.timeout_seconds,
        secret=secret,
        scan_mode=server.scan_mode,
        debounce_mode=server.debounce_mode,
        debounce_window_seconds=server.debounce_window_seconds,
        retry_attempts=server.retry_attempts,
        webhook_method=server.webhook_method,
        webhook_headers_json=server.webhook_headers_json,
        webhook_body_template=server.webhook_body_template,
    )
    client = build_client(verify_tls=server.verify_tls, timeout_seconds=server.timeout_seconds)
    try:
        adapter = create_adapter(runtime, client)
        result = await adapter.test()
    finally:
        await client.aclose()
    return ServerTestResponse(ok=result.ok, detail=result.detail)
```

- [ ] **Step 4: Mount the router in `create_app`**

Modify `mediascanmonitor/web/app.py` — add the import and the `include_router` call inside `create_app`
(keep every other sub-plan's `include_router` line; this is the shared merge point):

```python
from mediascanmonitor.web.api import servers as api_servers
```

```python
    app.include_router(api_servers.router)
```

> **B008 note:** the bugbear `extend-immutable-calls` table that allows FastAPI's `Depends(...)`/
> `Query(...)`/`Form(...)` parameter defaults is added by sub-plan 01 (Task 1) and already covers
> every marker used here, so `ruff check` is clean. Only if you execute out of order and `ruff`
> reports `B008` should you add/extend that table (see Global Constraints) — never `# noqa: B008`,
> and never a second `[tool.ruff.lint.flake8-bugbear]` table.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/web/test_api_servers.py -v`
Expected: PASS — 14 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/api/servers.py mediascanmonitor/web/app.py tests/web/test_api_servers.py && mypy mediascanmonitor/web/api/servers.py mediascanmonitor/web/app.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/api/servers.py mediascanmonitor/web/app.py tests/web/test_api_servers.py
git commit -m "feat(web): /api/servers CRUD + connectivity test endpoint"
```

---

### Task 6: Folders router (`web/api/folders.py`) + mount

**Files:**
- Create: `tests/web/test_api_folders.py`
- Create: `mediascanmonitor/web/api/folders.py`
- Modify: `mediascanmonitor/web/app.py`

**Interfaces:**
- Consumes: `APIRouter`/`Depends`/`HTTPException`/`status` (`fastapi`); `Repo` (`db/repo.py`);
  `FolderCreate`/`FolderUpdate` (`db/schemas.py`); `Engine` (`engine.py`); `FolderRead` (`web/api_schemas.py`);
  `get_repo`/`get_engine`/`require_api_auth` (`web/deps.py`); `apply_folder_*` (`web/writes.py`).
- Produces: `router` (prefix `/api/servers/{server_id}/folders`, guarded by `require_api_auth`).

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_folders.py`:

```python
"""/api/servers/{id}/folders CRUD (contract §E)."""

from starlette.testclient import TestClient


def _make_server(auth_client: TestClient) -> int:
    resp = auth_client.post("/api/servers", json={"name": "hook", "type": "webhook"})
    assert resp.status_code == 201
    return int(resp.json()["id"])


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/servers/1/folders").status_code == 401


def test_create_and_list_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    resp = auth_client.post(
        f"/api/servers/{server_id}/folders",
        json={"path": "/data/tv", "library_id": "2", "extensions": ["MKV", "mp4", "mkv"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["path"] == "/data/tv"
    assert body["extensions"] == ["mkv", "mp4"]  # normalized + sorted + deduped

    listed = auth_client.get(f"/api/servers/{server_id}/folders")
    assert listed.status_code == 200
    assert [f["id"] for f in listed.json()] == [body["id"]]


def test_get_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders", json={"path": "/data/movies"}
    ).json()
    got = auth_client.get(f"/api/servers/{server_id}/folders/{folder['id']}")
    assert got.status_code == 200
    assert got.json()["path"] == "/data/movies"


def test_patch_folder_replaces_extensions(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders",
        json={"path": "/data/tv", "extensions": ["mkv"]},
    ).json()
    resp = auth_client.patch(
        f"/api/servers/{server_id}/folders/{folder['id']}",
        json={"enabled": False, "extensions": ["avi", "flac"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["extensions"] == ["avi", "flac"]


def test_patch_rejects_relative_path(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders", json={"path": "/data/tv"}
    ).json()
    resp = auth_client.patch(
        f"/api/servers/{server_id}/folders/{folder['id']}", json={"path": "relative"}
    )
    assert resp.status_code == 422


def test_delete_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders", json={"path": "/data/tv"}
    ).json()
    assert auth_client.delete(f"/api/servers/{server_id}/folders/{folder['id']}").status_code == 204
    assert auth_client.get(f"/api/servers/{server_id}/folders/{folder['id']}").status_code == 404


def test_list_for_missing_server_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/servers/999/folders").status_code == 404


def test_get_folder_wrong_server_is_404(auth_client: TestClient) -> None:
    server_a = _make_server(auth_client)
    server_b = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_a}/folders", json={"path": "/data/tv"}
    ).json()
    assert auth_client.get(f"/api/servers/{server_b}/folders/{folder['id']}").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_api_folders.py -v`
Expected: FAIL — import/collection error (`web.api.folders` missing) or 404 on every route.

- [ ] **Step 3: Implement `web/api/folders.py`**

Create `mediascanmonitor/web/api/folders.py`:

```python
"""/api/servers/{server_id}/folders JSON CRUD (contract §E).

Folder mutations carry no secret, so the write-cores skip the token check. The parent server is
verified for every route (404 if absent), and a folder fetched for a server it does not belong to is
treated as not-found so ids cannot leak across servers.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.api_schemas import FolderRead
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_folder_delete,
    apply_folder_update,
)

router = APIRouter(
    prefix="/api/servers/{server_id}/folders",
    tags=["folders"],
    dependencies=[Depends(require_api_auth)],
)


async def _require_server(repo: Repo, server_id: int) -> None:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")


async def _owned_folder(repo: Repo, server_id: int, folder_id: int) -> None:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None or folder.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "folder not found")


@router.get("")
async def list_folders(server_id: int, repo: Repo = Depends(get_repo)) -> list[FolderRead]:
    await _require_server(repo, server_id)
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return [FolderRead.from_model(f) for f in folders]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(
    server_id: int,
    data: FolderCreate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> FolderRead:
    await _require_server(repo, server_id)
    folder = await apply_folder_create(repo, engine, server_id, data)
    return FolderRead.from_model(folder)


@router.get("/{folder_id}")
async def get_folder(
    server_id: int, folder_id: int, repo: Repo = Depends(get_repo)
) -> FolderRead:
    await _require_server(repo, server_id)
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None or folder.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "folder not found")
    return FolderRead.from_model(folder)


@router.patch("/{folder_id}")
async def update_folder(
    server_id: int,
    folder_id: int,
    data: FolderUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> FolderRead:
    await _require_server(repo, server_id)
    await _owned_folder(repo, server_id, folder_id)
    folder = await apply_folder_update(repo, engine, folder_id, data)
    return FolderRead.from_model(folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    server_id: int,
    folder_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> None:
    await _require_server(repo, server_id)
    await _owned_folder(repo, server_id, folder_id)
    await apply_folder_delete(repo, engine, folder_id)
```

- [ ] **Step 4: Mount the router in `create_app`**

Modify `mediascanmonitor/web/app.py`:

```python
from mediascanmonitor.web.api import folders as api_folders
```

```python
    app.include_router(api_folders.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/web/test_api_folders.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/api/folders.py mediascanmonitor/web/app.py tests/web/test_api_folders.py && mypy mediascanmonitor/web/api/folders.py mediascanmonitor/web/app.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/api/folders.py mediascanmonitor/web/app.py tests/web/test_api_folders.py
git commit -m "feat(web): /api/servers/{id}/folders CRUD"
```

---

### Task 7: Events router (`web/api/events.py`) + mount

**Files:**
- Create: `tests/web/test_api_events.py`
- Create: `mediascanmonitor/web/api/events.py`
- Modify: `mediascanmonitor/web/app.py`

**Interfaces:**
- Consumes: `APIRouter`/`Depends`/`Query` (`fastapi`); `EventsBus` (`observ/events_bus.py`);
  `EventRead` (`web/api_schemas.py`); `get_events_bus`/`require_api_auth` (`web/deps.py`).
- Produces: `router` (prefix `/api/events`, guarded by `require_api_auth`).

- [ ] **Step 1: Write the failing tests**

> Uses the shared `events_bus` fixture (the same instance the app was built with), so publishing to
> it then reading via `auth_client` exercises the wired bus. `EventRecord`'s constructor fields come
> from contract §G.

Create `tests/web/test_api_events.py`:

```python
"""/api/events/recent (contract §D / §G)."""

from starlette.testclient import TestClient

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus


def _record(server_id: int, *, ok: bool = True) -> EventRecord:
    return EventRecord(
        ts="2026-06-20T18:30:00+00:00",
        server_id=server_id,
        server_name=f"srv{server_id}",
        scan_mode="library",
        scan_key="lib:5",
        scan_path=None,
        library_id="5",
        event_type="created",
        file_path="/data/media/x.mkv",
        ok=ok,
        status_code=200 if ok else 500,
        detail="ok" if ok else "boom",
    )


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/events/recent").status_code == 401


def test_recent_empty(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/events/recent")
    assert resp.status_code == 200
    assert resp.json() == []


def test_recent_returns_published_records(auth_client: TestClient, events_bus: EventsBus) -> None:
    events_bus.publish(_record(1))
    events_bus.publish(_record(2, ok=False))
    resp = auth_client.get("/api/events/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["server_id"] for r in body] == [1, 2]  # newest-last
    assert body[1]["ok"] is False
    # redaction sanity: no secret-shaped field present.
    assert all("secret" not in r for r in body)


def test_recent_respects_limit(auth_client: TestClient, events_bus: EventsBus) -> None:
    for i in range(5):
        events_bus.publish(_record(i))
    resp = auth_client.get("/api/events/recent?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_recent_rejects_bad_limit(auth_client: TestClient) -> None:
    assert auth_client.get("/api/events/recent?limit=0").status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/web/test_api_events.py -v`
Expected: FAIL — import/collection error (`web.api.events` missing) or 404.

- [ ] **Step 3: Implement `web/api/events.py`**

Create `mediascanmonitor/web/api/events.py`:

```python
"""/api/events/recent — the recent-events read for the dashboard (contract §D/§G).

EventsBus.recent() is a non-blocking deque slice, so it is called directly (no to_thread). Every
record is mapped through the redacted EventRead — EventRecord itself carries no secret (rule 5).
"""

from fastapi import APIRouter, Depends, Query

from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.web.api_schemas import EventRead
from mediascanmonitor.web.deps import get_events_bus, require_api_auth

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
    dependencies=[Depends(require_api_auth)],
)


@router.get("/recent")
async def recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    events_bus: EventsBus = Depends(get_events_bus),
) -> list[EventRead]:
    return [EventRead.from_record(rec) for rec in events_bus.recent(limit)]
```

- [ ] **Step 4: Mount the router in `create_app`**

Modify `mediascanmonitor/web/app.py`:

```python
from mediascanmonitor.web.api import events as api_events
```

```python
    app.include_router(api_events.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/web/test_api_events.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/api/events.py mediascanmonitor/web/app.py tests/web/test_api_events.py && mypy mediascanmonitor/web/api/events.py mediascanmonitor/web/app.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/api/events.py mediascanmonitor/web/app.py tests/web/test_api_events.py
git commit -m "feat(web): /api/events/recent read endpoint"
```

---

## Verification (full gate, before the PR merges)

- [ ] Run the complete gate from the repo root (after `export PATH="$PWD/.venv/bin:$PATH"`):

```bash
ruff check . && ruff format --check . && mypy mediascanmonitor && pytest
```

Expected: all green. CI (`.github/workflows/ci.yml`) runs the same on Python 3.14 via `uv sync --locked`.

- [ ] Spot-confirm the cross-plan invariants this sub-plan owns:
  - **Secrets redacted (invariant 1):** `test_create_then_get_redacts_secret`,
    `test_server_read_redacts_secret`, `test_recent_returns_published_records` — no `secret`/ciphertext
    in any body.
  - **Every write rebuilds (invariant 4):** `test_create_calls_rebuild`,
    `test_delete_calls_rebuild_even_when_absent`, `test_folder_create_calls_rebuild`.
  - **Token-required 422 (§D):** `test_create_auth_type_without_secret_is_422`,
    `test_patch_clearing_secret_is_422`, plus the `web/writes.py` unit tests.
  - **Auth-closed by default (invariant 2):** the `test_requires_auth` test in each router file.
  - **No type special-casing (invariant 6):** `supported_scan_modes` + `SERVER_TYPE_SPECS` drive all
    per-type behavior; no router branches on a literal type.

---

## Self-Review

**Spec coverage (task spine):**

1. `FolderUpdate` + `repo.get_folder`/`update_folder` — Task 1 (validators mirror `FolderCreate`;
   `exclude_unset` tri-state; `extensions` present → replace `FileType` rows; filetypes force-loaded). ✓
2. `web/api_schemas.py` redacted read-models + `SERVER_TYPE_SPECS` — Task 2 (`has_secret`,
   `supported_scan_modes` from the registry, no secret field anywhere). ✓
3. `web/rebuild.py` `rebuild_engine` — Task 3 (`await engine.rebuild()`, swallow `RuntimeError`,
   log `web.rebuild_skipped`). ✓
4. `web/writes.py` shared write-cores — Task 4 (token-required 422 incl. `ServerUpdate` tri-state,
   off-thread write, rebuild). ✓
5. `web/api/servers.py` CRUD + `POST /{id}/test` — Task 5 (probe builds a `ServerRuntime` from the
   row + `resolve_secret`, `build_client`, `create_adapter`, `await adapter.test()`, `aclose` in
   `finally`). ✓
6. `web/api/folders.py` CRUD — Task 6 (server-scoped 404s; FolderUpdate via the folder write-cores). ✓
7. `web/api/events.py` `GET /api/events/recent` — Task 7 (`events_bus.recent(limit)` → `EventRead`). ✓

**Decisions made (flagged for the executor):**

- **`test` endpoint probe-adapter construction:** there is no existing `Server → ServerRuntime` helper,
  and `build_runtime_config` only assembles *enabled* servers into a whole-config snapshot. To let an
  operator test a server **before enabling it**, the handler constructs the `ServerRuntime` inline from
  the stored row with full keyword args (mypy-clean, no dict splat), decrypting the secret in memory via
  `repo.resolve_secret`, and owns a one-shot `httpx` client closed in a `finally`. This deliberately does
  **not** reuse the engine's long-lived clients.
- **`has_secret` from `secret is not None and secret != ""`** in `apply_server_create` / `bool(dumped["secret"])`
  in `apply_server_update`: an empty-string secret is treated as "no secret" for the token-required gate, so a
  blank token can't slip past the 422.
- **Folder ownership 404s:** a folder fetched/updated/deleted under a `server_id` it doesn't belong to is
  treated as not-found (prevents cross-server id traversal).
- **`EventRead` mapping (vs. raw `EventRecord`):** added a Pydantic `EventRead.from_record` to keep the
  response a validated boundary model (rule 7) rather than serializing the dataclass directly.

**Placeholder scan:** none — complete code + exact commands throughout.

**Dependencies on sub-plan 01 (stated, not built here):** `create_app`/`web/deps.py`
(`get_repo`/`get_engine`/`get_events_bus`/`require_api_auth`), `observ/events_bus.py`
(`EventsBus`/`EventRecord`), and `tests/web/conftest.py` (`repo`/`events_bus`/`engine`/`app`/`client`/
`auth_client`). The only file shared with other sub-plans is `web/app.py` (append-only `include_router`
lines).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-phase3-02-rest-api.md`. Two execution
options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session using `executing-plans`, batched with checkpoints.

Which approach?
</content>
</invoke>
