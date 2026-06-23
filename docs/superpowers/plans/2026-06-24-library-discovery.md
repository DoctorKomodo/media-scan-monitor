# Library Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users fetch a media backend's libraries (id + human name) and pick one from a dialog instead of hand-typing opaque library ids — a general adapter capability, implemented for Audiobookshelf first.

**Architecture:** A new opt-in capability on the `ServerAdapter` ABC (`supports_library_discovery` ClassVar + a defaulted `list_libraries()`), surfaced through two web endpoints that twin the existing Test flow, rendered as a per-row "Fetch → picker dialog" affordance mirroring the folder Browse picker. A new nullable `folder.library_name` column (Alembic `0002`) remembers the chosen name for display; `library_id` stays the source of truth.

**Tech Stack:** Python 3.14, FastAPI + Jinja2 + htmx, SQLModel/SQLAlchemy, Pydantic, Alembic, httpx (+ respx in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-06-23-library-discovery-design.md` (read it for rationale; this plan is the authoritative build order).

## Global Constraints

These bind every task (copied from CLAUDE.md + the spec):

- **PEP 649 annotations** — never add `from __future__ import annotations`; leave forward refs unquoted. A name in a runtime-introspected annotation (SQLModel/Pydantic) must be importable at runtime.
- **Enums subclass `StrEnum`**, never `(str, Enum)`.
- **Ruff select is exactly `E,F,I,UP,B,C4,SIM,RUF`** (B ignored under `tests/**`). No `# noqa` for unselected rules. First-party import group is `mediascanmonitor` (blank line before it).
- **`mypy --strict` must be clean**; full type hints on every signature.
- **Rule 2 — one file per backend / no special-casing:** per-type facts come from the adapter or `SERVER_TYPE_SPECS`/registry, never a literal type-name branch in routers/templates.
- **Rule 3 — validate every external boundary** with Pydantic; never pass raw dicts. Backend JSON parses through a Pydantic model.
- **Rule 5 — security:** the token stays in the `Authorization` header and POST body, never the URL or logs. Read-models never carry the secret.
- **Rule 7 — explicit migrations:** schema change ships as an Alembic revision; never `create_all`.
- **Branch:** `feat/library-discovery` (already exists with the spec commit). Each task commits with the footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
  ```
- **Gate (run before every commit):** `ruff format . && ruff check . && mypy mediascanmonitor && pytest`.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `mediascanmonitor/servers/base.py` | `LibraryOption`, `LibraryListResult`, ABC capability flag + defaulted `list_libraries()` | 1 |
| `mediascanmonitor/servers/audiobookshelf.py` | ABS `list_libraries()` + response Pydantic models; flag = True | 1 |
| `mediascanmonitor/db/models.py` | `Folder.library_name` column | 2 |
| `mediascanmonitor/db/schemas.py` | `FolderCreate.library_name` | 2 |
| `mediascanmonitor/web/api_schemas.py` | `FolderRead.library_name` + `from_model` wiring | 2 |
| `mediascanmonitor/migrations/versions/0002_folder_library_name.py` | the migration | 2 |
| `mediascanmonitor/db/repo.py` | carry `library_name` through `create_folder` + `_set_server_folders` | 2 |
| `mediascanmonitor/web/serverprobe.py` | **renamed from `servertest.py`** (it now hosts more than `test`); extract shared `_with_adapter()` lifecycle; add `run_library_listing()` beside `run_connectivity_test()` | 3 |
| `mediascanmonitor/web/templates/_library_options.html` | the dialog-body partial the endpoints render | 3 |
| `mediascanmonitor/web/pages.py` | two endpoints, `_parse_folder_rows` library_name, `_type_specs` flag, `server_detail` context flag | 3 (endpoints + parse), 4 (flag plumbing) |
| `mediascanmonitor/web/templates/_library_picker.html` | the shared `<dialog>` shell | 4 |
| `mediascanmonitor/web/templates/_folder_editor.html` | Fetch button, hidden field, name label, editor-root flags | 4 |
| `mediascanmonitor/web/templates/_folder_rows_script.html` | picker wiring JS | 4 |
| `mediascanmonitor/web/templates/server_new.html` | `apply()` toggles the discovery flag per type | 4 |
| `mediascanmonitor/web/static/app.css` | Fetch button + dialog + name-label styles | 4 |
| `docs/FOLLOWUPS.md` | reconcile the satisfied discovery item | 4 |

---

## Task 1: Adapter capability + Audiobookshelf implementation

**Files:**
- Modify: `mediascanmonitor/servers/base.py`
- Modify: `mediascanmonitor/servers/audiobookshelf.py`
- Test: `tests/servers/test_base.py` (new), `tests/servers/test_audiobookshelf.py`

**Interfaces:**
- Produces:
  - `LibraryOption(id: str, name: str)` — frozen slots dataclass in `servers/base.py`.
  - `LibraryListResult(ok: bool, detail: str, libraries: tuple[LibraryOption, ...] = ())` — frozen slots dataclass with `__test__ = False`.
  - `ServerAdapter.supports_library_discovery: ClassVar[bool] = False`.
  - `async ServerAdapter.list_libraries(self) -> LibraryListResult` (default returns `ok=False, detail="not supported"`).
  - `AudiobookshelfAdapter.supports_library_discovery = True` and a working `list_libraries()`.

- [ ] **Step 1: Write the failing tests for the base default + ABS happy/sad paths**

Create `tests/servers/test_base.py` (pytest is `asyncio_mode = "auto"`, so a bare `async def`
test needs no marker; reuse the `client` async-client fixture + `make_plex_runtime` helper from
`tests/servers/conftest.py`):

```python
"""ServerAdapter library-discovery default capability (the webhook adapter inherits it)."""

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.servers import registry

from .conftest import make_plex_runtime


async def test_default_list_libraries_is_unsupported(client: httpx.AsyncClient) -> None:
    # The webhook adapter does not override list_libraries(), so it inherits the ABC default.
    runtime = make_plex_runtime(
        type=ServerType.webhook, base_url="", scan_mode=ScanMode.library, secret=None
    )
    adapter = registry.create_adapter(runtime, client)
    assert adapter.supports_library_discovery is False
    result = await adapter.list_libraries()
    assert result.ok is False
    assert result.detail == "not supported"
    assert result.libraries == ()
```

> NOTE to implementer: `make_plex_runtime` is the shared runtime builder (despite the name it
> takes a `type=` override — confirm its signature in `tests/servers/conftest.py`). If a webhook
> runtime needs `webhook_*` fields it doesn't default, pass them; the assertion contract
> (False / "not supported" / `()`) is what matters.

Add to `tests/servers/test_audiobookshelf.py` (it already imports `httpx`, `respx`, `BASE`, `abs_runtime`, `client`):

```python
LIBRARIES = f"{BASE}/api/libraries"


@respx.mock
async def test_list_libraries_parses_id_and_name(client: httpx.AsyncClient) -> None:
    respx.get(LIBRARIES).mock(
        return_value=httpx.Response(
            200,
            json={"libraries": [
                {"id": "lib_abc", "name": "Audiobooks", "mediaType": "book"},
                {"id": "lib_def", "name": "Podcasts"},
            ]},
        )
    )
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok"), client)
    result = await adapter.list_libraries()
    assert result.ok is True
    assert [(o.id, o.name) for o in result.libraries] == [
        ("lib_abc", "Audiobooks"), ("lib_def", "Podcasts")
    ]


@respx.mock
async def test_list_libraries_sends_bearer_header(client: httpx.AsyncClient) -> None:
    route = respx.get(LIBRARIES).mock(return_value=httpx.Response(200, json={"libraries": []}))
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok-secret"), client)
    await adapter.list_libraries()
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-secret"


@respx.mock
async def test_list_libraries_maps_401_to_error(client: httpx.AsyncClient) -> None:
    respx.get(LIBRARIES).mock(return_value=httpx.Response(401))
    result = await AudiobookshelfAdapter(abs_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail == "HTTP 401"
    assert result.libraries == ()


@respx.mock
async def test_list_libraries_maps_connection_error(client: httpx.AsyncClient) -> None:
    respx.get(LIBRARIES).mock(side_effect=httpx.ConnectError("boom"))
    result = await AudiobookshelfAdapter(abs_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail.startswith("ConnectError")


@respx.mock
async def test_list_libraries_maps_garbage_body(client: httpx.AsyncClient) -> None:
    respx.get(LIBRARIES).mock(return_value=httpx.Response(200, text="not json"))
    result = await AudiobookshelfAdapter(abs_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail == "unexpected response from Audiobookshelf"


def test_abs_supports_library_discovery() -> None:
    assert AudiobookshelfAdapter.supports_library_discovery is True
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/servers/test_base.py tests/servers/test_audiobookshelf.py -k "library or supports" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'list_libraries'` / `supports_library_discovery`.

- [ ] **Step 3: Add the value objects + ABC capability to `base.py`**

In `mediascanmonitor/servers/base.py`, after the `TestResult` dataclass and before `class ServerAdapter`, add:

```python
@dataclass(frozen=True, slots=True)
class LibraryOption:
    """One selectable backend library: an opaque id plus a human label."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class LibraryListResult:
    """Outcome of a list_libraries() probe — mirrors TestResult's ok/detail shape."""

    __test__ = False  # not a pytest class despite living beside Test* names

    ok: bool
    detail: str
    libraries: tuple[LibraryOption, ...] = ()
```

In `class ServerAdapter`, add the ClassVar next to `supported_scan_modes` and a defaulted method after `test()`:

```python
    supports_library_discovery: ClassVar[bool] = False
```

```python
    async def list_libraries(self) -> LibraryListResult:
        """List selectable libraries (id + name). Default: the backend has no concept of one."""
        return LibraryListResult(ok=False, detail="not supported")
```

Update the `class ServerAdapter` docstring line "Subclasses MUST set the two ClassVars and implement the two async methods." to: "Subclasses MUST set `server_type` + `supported_scan_modes` and implement `trigger()` + `test()`. `list_libraries()` is optional — override it and set `supports_library_discovery = True` to enable the UI's library picker (default: unsupported)."

- [ ] **Step 4: Implement ABS `list_libraries()`**

In `mediascanmonitor/servers/audiobookshelf.py`:

Add `from pydantic import BaseModel` to the **third-party** import group (beside the existing
`import httpx`, blank line before the first-party `mediascanmonitor` group — `ruff check --fix`
autofixes I001 if the placement is off, so don't assert "clean" until after `ruff format`).
Extend the existing base import to pull in the new names:

```python
from mediascanmonitor.servers.base import (
    LibraryListResult,
    LibraryOption,
    ServerAdapter,
    TestResult,
    TriggerResult,
)
```

(Replace the existing `from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult` line with the grouped import above.)

After the imports, add the response models. Pydantic's default `extra` is already `ignore`, so
unknown fields like `mediaType` are dropped without any `model_config` — keep them minimal:

```python
class _AbsLibrary(BaseModel):
    id: str
    name: str


class _AbsLibrariesResponse(BaseModel):
    libraries: list[_AbsLibrary]
```

Set the flag on the class (next to `supported_scan_modes`):

```python
    supports_library_discovery: ClassVar[bool] = True
```

Add the method (after `test()`):

```python
    async def list_libraries(self) -> LibraryListResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/libraries"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return LibraryListResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if not resp.is_success:
            return LibraryListResult(ok=False, detail=f"HTTP {resp.status_code}")
        try:
            parsed = _AbsLibrariesResponse.model_validate(resp.json())
        except ValueError:
            # covers httpx's json.JSONDecodeError (a ValueError) and Pydantic ValidationError.
            return LibraryListResult(ok=False, detail="unexpected response from Audiobookshelf")
        return LibraryListResult(
            ok=True,
            detail="",
            libraries=tuple(LibraryOption(id=lib.id, name=lib.name) for lib in parsed.libraries),
        )
```

Extend the module docstring's "VERIFY AT IMPLEMENT-TIME" line to also cover the
`GET /api/libraries` path and the `libraries[].{id,name}` response shape.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `pytest tests/servers/test_base.py tests/servers/test_audiobookshelf.py -v`
Expected: PASS (all, including the pre-existing ABS scan/test cases).

- [ ] **Step 6: Run the gate and commit**

```bash
ruff format . && ruff check . && mypy mediascanmonitor && pytest
git add mediascanmonitor/servers/base.py mediascanmonitor/servers/audiobookshelf.py tests/servers/test_base.py tests/servers/test_audiobookshelf.py
git commit -m "feat(servers): library-discovery capability + Audiobookshelf list_libraries()"
```

---

## Task 2: Persistence — `library_name` column, schemas, migration, repo

**Files:**
- Modify: `mediascanmonitor/db/models.py` (Folder)
- Modify: `mediascanmonitor/db/schemas.py` (FolderCreate)
- Modify: `mediascanmonitor/web/api_schemas.py` (FolderRead + from_model)
- Modify: `mediascanmonitor/db/repo.py` (`_set_server_folders`, `create_folder`)
- Create: `mediascanmonitor/migrations/versions/0002_folder_library_name.py`
- Test: `tests/db/test_repo.py`, `tests/db/test_migrations.py` (new)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `Folder.library_name: str | None` (DB column, nullable).
  - `FolderCreate.library_name: str | None = None`.
  - `FolderRead.library_name: str | None` (set by `from_model`).

- [ ] **Step 1: Write the failing repo + migration tests**

Add to `tests/db/test_repo.py` (it imports `FolderCreate`, `ServerCreate`, `ServerUpdate`, `make_server`, `repo`):

```python
def test_create_folder_persists_library_name(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(
        server.id,
        FolderCreate(path="/data/abs", library_id="lib_x", library_name="Audiobooks"),
    )
    [folder] = repo.list_folders(server.id)
    assert (folder.library_id, folder.library_name) == ("lib_x", "Audiobooks")


def test_create_folder_defaults_library_name_to_none(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(server.id, FolderCreate(path="/data/tv", library_id="2"))
    [folder] = repo.list_folders(server.id)
    assert folder.library_name is None


def test_update_server_with_folders_persists_library_name(repo: Repo) -> None:
    server = repo.create_server_with_folders(make_server(name="abs"), [])
    assert server.id is not None
    repo.update_server_with_folders(
        server.id,
        ServerUpdate(),
        [FolderCreate(path="/data/pods", library_id="lib_y", library_name="Podcasts")],
    )
    [folder] = repo.list_folders(server.id)
    assert (folder.library_id, folder.library_name) == ("lib_y", "Podcasts")
```

Create `tests/db/test_migrations.py`:

```python
"""The DB migrates to head and the folder table carries every expected column."""

from pathlib import Path

from sqlalchemy import inspect

from mediascanmonitor.db.session import init_db


def test_folder_table_has_library_name_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("folder")}
    assert "library_name" in columns
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/db/test_repo.py -k library_name tests/db/test_migrations.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'library_name'` (FolderCreate) and `assert 'library_name' in columns` fails.

- [ ] **Step 3: Add the model column**

In `mediascanmonitor/db/models.py`, in `class Folder`, add after the `library_id` line:

```python
    library_name: str | None = None  # human label for library_id; display-only, set via the picker
```

- [ ] **Step 4: Add the schema + read-model fields**

In `mediascanmonitor/db/schemas.py`, in `class FolderCreate`, add after `library_id`:

```python
    library_name: str | None = None
```

In `mediascanmonitor/web/api_schemas.py`, in `class FolderRead`, add after `library_id: str | None`:

```python
    library_name: str | None
```

and in `FolderRead.from_model`, add to the `cls(...)` call:

```python
            library_name=folder.library_name,
```

- [ ] **Step 5: Carry `library_name` through the repo write paths**

In `mediascanmonitor/db/repo.py`, in `_set_server_folders`, change the `Folder(...)` construction to:

```python
        folder = Folder(
            path=data.path,
            library_id=data.library_id,
            library_name=data.library_name,
            enabled=data.enabled,
        )
```

In `create_folder`, change the `Folder(...)` construction to:

```python
            folder = Folder(
                server_id=server_id,
                path=data.path,
                library_id=data.library_id,
                library_name=data.library_name,
                enabled=data.enabled,
            )
```

- [ ] **Step 6: Write the migration**

Create `mediascanmonitor/migrations/versions/0002_folder_library_name.py`:

```python
"""folder.library_name

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("library_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.drop_column("library_name")
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run: `pytest tests/db/test_repo.py tests/db/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 8: Run the gate and commit**

```bash
ruff format . && ruff check . && mypy mediascanmonitor && pytest
git add mediascanmonitor/db/models.py mediascanmonitor/db/schemas.py mediascanmonitor/web/api_schemas.py mediascanmonitor/db/repo.py mediascanmonitor/migrations/versions/0002_folder_library_name.py tests/db/test_repo.py tests/db/test_migrations.py
git commit -m "feat(db): folder.library_name column + migration 0002 (display label for library_id)"
```

---

## Task 3: Web endpoints + shared listing helper + options partial

**Files:**
- Rename: `mediascanmonitor/web/servertest.py` → `mediascanmonitor/web/serverprobe.py` (use `git mv`), then extract `_with_adapter`, add `run_library_listing`
- Modify: `mediascanmonitor/web/pages.py` (rename the import, two routes, `_parse_folder_rows`)
- Modify: `mediascanmonitor/web/api/servers.py` (rename the import only)
- Create: `mediascanmonitor/web/templates/_library_options.html`
- Test: `tests/web/test_ui_libraries.py` (new)

**Interfaces:**
- Consumes: `LibraryListResult` + `ServerAdapter` + `supports_library_discovery` (Task 1); `FolderCreate.library_name` (Task 2); existing `runtime_from_create`, `runtime_from_server`, `build_client`, `create_adapter`, `run_connectivity_test`.
- Produces:
  - `run_library_listing(runtime: ServerRuntime) -> LibraryListResult` in `web/serverprobe.py` (the home of the twin Test helper — no new module; both now share one `_with_adapter` client-lifecycle primitive).
  - `POST /ui/servers/libraries` (unsaved-config path) and `POST /ui/servers/{server_id}/libraries` (stored path), both rendering `_library_options.html`.

**Why rename + no new module (consolidation):** `run_library_listing` and `run_connectivity_test`
differ only in *which* adapter method they call; the build-client / try / `aclose()` lifecycle is
identical, and the library endpoints already need `runtime_from_*` from the same file. Factoring
the lifecycle into one `_with_adapter` helper and keeping both probes in one module is the
project's "don't maintain duplicate code" rule applied. The module is renamed `servertest.py` →
`serverprobe.py` because it no longer hosts only `test` — both functions are read-only *probes*
of a live (possibly unsaved) server config. Only `pages.py` and `api/servers.py` import it; no
test imports the module by name.

- [ ] **Step 1: Write the failing integration tests**

Create `tests/web/test_ui_libraries.py`:

```python
"""Library-discovery endpoints: unsaved-config + stored, success / not-supported / error."""

import httpx
import respx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import ServerCreate

ABS_BASE = "http://abs:13378"


def _abs_form() -> dict[str, str]:
    return {"type": "audiobookshelf", "base_url": ABS_BASE, "secret": "tok", "timeout_seconds": "10"}


@respx.mock
def test_libraries_unsaved_renders_options(auth_client: httpx.Client) -> None:
    respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(200, json={"libraries": [{"id": "lib_abc", "name": "Audiobooks"}]})
    )
    resp = auth_client.post("/ui/servers/libraries", data=_abs_form())
    assert resp.status_code == 200
    assert "Audiobooks" in resp.text
    assert "lib_abc" in resp.text


def test_libraries_unsaved_not_supported_for_webhook(auth_client: httpx.Client) -> None:
    resp = auth_client.post(
        "/ui/servers/libraries", data={"type": "webhook", "base_url": "", "secret": ""}
    )
    assert resp.status_code == 200
    assert "no libraries" in resp.text.lower()


@respx.mock
def test_libraries_unsaved_renders_error_on_401(auth_client: httpx.Client) -> None:
    respx.get(f"{ABS_BASE}/api/libraries").mock(return_value=httpx.Response(401))
    resp = auth_client.post("/ui/servers/libraries", data=_abs_form())
    assert resp.status_code == 200
    assert "HTTP 401" in resp.text


@respx.mock
def test_libraries_stored_uses_saved_secret(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    server = repo.create_server(
        ServerCreate(name="ABS", type=ServerType.audiobookshelf, base_url=ABS_BASE, secret="stored-tok")
    )
    route = respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(200, json={"libraries": [{"id": "lib_z", "name": "Stored Lib"}]})
    )
    resp = auth_client.post(f"/ui/servers/{server.id}/libraries", data={"secret": ""})
    assert resp.status_code == 200
    assert "Stored Lib" in resp.text
    assert route.calls.last.request.headers["Authorization"] == "Bearer stored-tok"


@respx.mock
def test_libraries_stored_typed_token_overrides_stored(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    # The deliberate enhancement over ui_test_server: a freshly-typed token in the form wins.
    server = repo.create_server(
        ServerCreate(name="ABS", type=ServerType.audiobookshelf, base_url=ABS_BASE, secret="stored-tok")
    )
    route = respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(200, json={"libraries": []})
    )
    auth_client.post(f"/ui/servers/{server.id}/libraries", data={"secret": "typed-override"})
    assert route.calls.last.request.headers["Authorization"] == "Bearer typed-override"


def test_libraries_stored_404_for_missing(auth_client: httpx.Client) -> None:
    resp = auth_client.post("/ui/servers/9999/libraries", data={"secret": ""})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


def test_parse_folder_rows_reads_library_name() -> None:
    # The hidden folder-<i>-library_name field round-trips into FolderCreate; empty → None.
    from starlette.datastructures import FormData

    from mediascanmonitor.web.pages import _parse_folder_rows

    named = _parse_folder_rows(
        FormData([("folder-0-path", "/data/abs"), ("folder-0-library_id", "lib_x"),
                  ("folder-0-library_name", "Audiobooks")])
    )
    assert (named[0].library_id, named[0].library_name) == ("lib_x", "Audiobooks")

    blank = _parse_folder_rows(
        FormData([("folder-0-path", "/data/tv"), ("folder-0-library_id", "2"),
                  ("folder-0-library_name", "")])
    )
    assert blank[0].library_name is None
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/web/test_ui_libraries.py -v`
Expected: FAIL — 404/405 (routes don't exist yet).

- [ ] **Step 3: Rename the module, then extract the shared lifecycle + add `run_library_listing`**

First rename the file and repoint its two importers (the module name no longer fits now that it
hosts a non-`test` probe):

```bash
git mv mediascanmonitor/web/servertest.py mediascanmonitor/web/serverprobe.py
```

Update the import in `mediascanmonitor/web/api/servers.py` — change
`from mediascanmonitor.web.servertest import ...` to
`from mediascanmonitor.web.serverprobe import ...` (same imported names). Update its docstring
reference if it names the module. (The `pages.py` import is updated in Step 5.)

Update the renamed module's own docstring (it currently says "Shared connectivity-test helper…")
to describe both probes, e.g. "Shared one-shot *probes* of a live server config (connectivity
test + library listing): build a throwaway runtime/adapter, run one operation, always close the
client. The JSON `/api/*` and HTML `/ui/*` surfaces both call these so they never drift."

Then in `mediascanmonitor/web/serverprobe.py`, add the typing + base imports to the existing groups:

```python
from collections.abc import Awaitable, Callable

from mediascanmonitor.servers.base import LibraryListResult, ServerAdapter
```

(`Awaitable`/`Callable` join the stdlib group at the top; `LibraryListResult`/`ServerAdapter`
join the existing `mediascanmonitor.servers...` first-party group.)

Add the shared primitive and the new probe, and refactor `run_connectivity_test` to use it —
so the build-client / `aclose()` lifecycle lives in exactly one place:

```python
async def _with_adapter[T](
    runtime: ServerRuntime, fn: Callable[[ServerAdapter], Awaitable[T]]
) -> T:
    """Build a throwaway adapter for ``runtime``, run ``fn``, and ALWAYS close the client."""
    client = build_client(verify_tls=runtime.verify_tls, timeout_seconds=runtime.timeout_seconds)
    try:
        return await fn(create_adapter(runtime, client))
    finally:
        await client.aclose()


async def run_connectivity_test(runtime: ServerRuntime) -> ServerTestResponse:
    """Probe a server via its registered adapter, always closing the client."""
    result = await _with_adapter(runtime, lambda adapter: adapter.test())
    return ServerTestResponse(ok=result.ok, detail=result.detail)


async def run_library_listing(runtime: ServerRuntime) -> LibraryListResult:
    """List a server's libraries via its adapter (twin of run_connectivity_test)."""

    async def _list(adapter: ServerAdapter) -> LibraryListResult:
        if not adapter.supports_library_discovery:
            return LibraryListResult(ok=False, detail="This server type has no libraries to list.")
        return await adapter.list_libraries()

    return await _with_adapter(runtime, _list)
```

(Replace the existing `run_connectivity_test` body with the version above; PEP 695 `[T]`
generics are fine on Python 3.14 + `mypy --strict`.)

- [ ] **Step 4: Write the options partial**

Create `mediascanmonitor/web/templates/_library_options.html`:

```html
{# Picker-dialog body. Rendered by the library endpoints; swapped into #library-listing.
   On success: a radio per library (the picker JS reads value=id + data-lib-name). #}
{% if result.ok and result.libraries %}
<ul class="lib-options" role="list">
  {% for lib in result.libraries %}
  <li>
    <label class="lib-option">
      <input type="radio" name="lib-choice" value="{{ lib.id }}" data-lib-name="{{ lib.name }}">
      <span class="lib-name">{{ lib.name }}</span>
      <span class="lib-id muted">{{ lib.id }}</span>
    </label>
  </li>
  {% endfor %}
</ul>
{% elif result.ok %}
<p class="lib-empty muted">No libraries found.</p>
{% else %}
<p class="lib-error" role="alert">Couldn't list libraries: {{ result.detail }}</p>
{% endif %}
```

- [ ] **Step 5: Add the endpoints + parse to `pages.py`**

In `mediascanmonitor/web/pages.py`, repoint the import to the renamed module and add
`run_library_listing` (it already imports `run_connectivity_test`, `runtime_from_create`,
`runtime_from_server`):

```python
from mediascanmonitor.web.serverprobe import (
    run_connectivity_test,
    run_library_listing,
    runtime_from_create,
    runtime_from_server,
)
```

and add `LibraryListResult` to the adapter-base imports:

```python
from mediascanmonitor.servers.base import LibraryListResult
```

In `_parse_folder_rows`, read the new hidden field. Change the `FolderCreate(...)` construction to include `library_name`:

```python
        folders.append(
            FolderCreate(
                path=path,
                library_id=library_id or None,
                library_name=str(form.get(f"folder-{i}-library_name") or "").strip() or None,
                extensions=_split_extensions(str(form.get(f"folder-{i}-extensions") or "")),
                enabled=f"folder-{i}-enabled" in form,
            )
        )
```

Add a render helper next to `_test_result_response`:

```python
def _library_options_response(
    request: Request, templates: Jinja2Templates, result: LibraryListResult
) -> Response:
    return templates.TemplateResponse(
        request=request, name="_library_options.html", context={"result": result}
    )
```

Add the two routes (place them next to the test routes, after `ui_test_server`):

```python
@router.post("/ui/servers/libraries")
async def ui_list_libraries_config(
    request: Request,
    type: str = Form(...),
    base_url: str = Form(""),
    secret: str = Form(""),
    verify_tls: bool = Form(False),
    timeout_seconds: float = Form(10.0),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Unsaved "fetch before save": probe the config the new-server form currently holds.
    try:
        data = ServerCreate(
            name="lib-fetch",
            type=ServerType(type),
            base_url=base_url,
            secret=secret or None,
            verify_tls=verify_tls,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return _library_options_response(
            request, templates, LibraryListResult(ok=False, detail=str(exc))
        )
    result = await run_library_listing(runtime_from_create(data))
    return _library_options_response(request, templates, result)


@router.post("/ui/servers/{server_id}/libraries")
async def ui_list_libraries(
    request: Request,
    server_id: int,
    secret: str = Form(""),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Stored path: a freshly-typed token (replace-in-progress) overrides the stored secret;
    # an EMPTY form secret means "fall back to the stored token", never "no auth".
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        return _library_options_response(
            request, templates, LibraryListResult(ok=False, detail=f"server {server_id} not found")
        )
    stored = await asyncio.to_thread(repo.resolve_secret, server)
    result = await run_library_listing(runtime_from_server(server, secret or stored))
    return _library_options_response(request, templates, result)
```

> Route ordering: FastAPI matches the literal `/ui/servers/libraries` before
> `/ui/servers/{server_id}/libraries` only if the literal route is declared first OR the
> converter rejects "libraries" — `{server_id: int}` will not match "libraries", so order is
> safe either way. Declare the literal first to match the existing `/servers/new` convention.

- [ ] **Step 6: Run the tests to confirm they pass**

Run: `pytest tests/web/test_ui_libraries.py -v`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```bash
ruff format . && ruff check . && mypy mediascanmonitor && pytest
git add -A mediascanmonitor/web/serverprobe.py mediascanmonitor/web/api/servers.py mediascanmonitor/web/templates/_library_options.html mediascanmonitor/web/pages.py tests/web/test_ui_libraries.py
git commit -m "feat(web): rename servertest→serverprobe, add library listing sharing one adapter lifecycle"
```

> `git add -A` ensures the rename (the deleted `servertest.py` + new `serverprobe.py`) is staged
> as a move. Verify with `git status` that it shows `renamed: servertest.py -> serverprobe.py`.

---

## Task 4: Folder-editor UI — Fetch button, picker dialog, flag plumbing

**Files:**
- Modify: `mediascanmonitor/web/pages.py` (`_type_specs`, `server_detail` context)
- Modify: `mediascanmonitor/web/templates/_folder_editor.html`
- Create: `mediascanmonitor/web/templates/_library_picker.html`
- Modify: `mediascanmonitor/web/templates/_folder_rows_script.html`
- Modify: `mediascanmonitor/web/templates/server_new.html`
- Modify: `mediascanmonitor/web/static/app.css`
- Modify: `docs/FOLLOWUPS.md`
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: the `/ui/servers/libraries` + `/ui/servers/{id}/libraries` endpoints (Task 3); `supports_library_discovery` from the registry (Task 1); `f.library_name` on `FolderRead` (Task 2).
- Produces: the rendered Fetch affordance. No new Python interface for later tasks (final task).

- [ ] **Step 1: Write the failing UI assertion tests**

Add to `tests/web/test_pages.py` (it imports `ServerCreate`, `FolderCreate`, `ServerType`, `repo`, `auth_client`):

```python
def test_new_page_exposes_discovery_in_type_specs(auth_client: httpx.Client) -> None:
    body = auth_client.get("/servers/new").text
    assert "supports_library_discovery" in body  # serialized into #type-specs for the per-type JS
    assert "data-fetch-lib" in body  # the Fetch button is always rendered (JS/CSS gate visibility)


def test_abs_detail_flags_library_discovery_on(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    server = repo.create_server(
        ServerCreate(name="ABS", type=ServerType.audiobookshelf, base_url="http://abs:13378", secret="t")
    )
    repo.create_folder(
        server.id,
        FolderCreate(path="/data/abs", library_id="lib_x", library_name="Audiobooks", extensions=["m4b"]),
    )
    body = auth_client.get(f"/servers/{server.id}").text
    assert 'data-library-discovery="true"' in body
    assert "data-fetch-lib" in body
    assert "Audiobooks" in body  # the saved friendly name renders as the companion label


def test_webhook_detail_flags_library_discovery_off(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    hook = repo.create_server(ServerCreate(name="hook", type=ServerType.webhook))
    body = auth_client.get(f"/servers/{hook.id}").text
    assert 'data-library-discovery="false"' in body
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/web/test_pages.py -k "discovery or library" -v`
Expected: FAIL — markers absent.

- [ ] **Step 3: Add the discovery flag to `_type_specs` + `server_detail` context**

In `mediascanmonitor/web/pages.py`, extend `_type_specs()` (it already has `registry` imported) so each entry gains the registry-derived flag:

```python
def _type_specs() -> dict[str, dict[str, bool]]:
    """Serialize SERVER_TYPE_SPECS for the template/JS (the one place per-type rules live, §D)."""
    return {
        server_type.value: {
            "requires_secret": spec.requires_secret,
            "requires_base_url": spec.requires_base_url,
            "is_webhook": spec.is_webhook,
            "supports_library_discovery": registry.get_adapter_class(
                server_type
            ).supports_library_discovery,
        }
        for server_type, spec in SERVER_TYPE_SPECS.items()
    }
```

In `server_detail`, add to the context dict:

```python
            "library_discovery": registry.get_adapter_class(server.type).supports_library_discovery,
```

- [ ] **Step 4: Update `_folder_editor.html`**

At the top of the file (before the opening `<div class="folder-editor"...>`), set a defaulted flag and the per-page endpoint:

```html
{% set ld = library_discovery | default(false, true) %}
{% set lib_endpoint = ("/ui/servers/%d/libraries" % server.id) if (server is defined and server) else "/ui/servers/libraries" %}
```

Change the editor root opening tag to carry both attributes:

```html
<div class="folder-editor" data-folder-editor
     data-library-discovery="{{ 'true' if ld else 'false' }}"
     data-library-endpoint="{{ lib_endpoint }}">
```

Replace the bare library input in **all three** row blocks (the `{% for %}` existing-row, the `{% else %}` empty-row, and the `<template>`) with the library cell. For the existing-row block use `loop.index0`; for the empty-row block use `0`; for the template use `__I__`. Existing-row version:

```html
      <div class="nf-library" data-library-field>
        <input type="text" class="nf-library-id" name="folder-{{ loop.index0 }}-library_id"
               value="{{ f.library_id or '' }}" placeholder="—" aria-label="Library id">
        <input type="hidden" name="folder-{{ loop.index0 }}-library_name" value="{{ f.library_name or '' }}">
        <button type="button" class="nf-fetch-lib" data-fetch-lib>Fetch</button>
        {% if f.library_name %}<span class="nf-library-name muted">{{ f.library_name }}</span>{% endif %}
      </div>
```

Empty-row version (no `f`, fixed index 0, no name label):

```html
      <div class="nf-library" data-library-field>
        <input type="text" class="nf-library-id" name="folder-0-library_id"
               placeholder="—" aria-label="Library id">
        <input type="hidden" name="folder-0-library_name" value="">
        <button type="button" class="nf-fetch-lib" data-fetch-lib>Fetch</button>
      </div>
```

Template version (inside `<template data-folder-row-tpl>`, index `__I__`, no name label):

```html
      <div class="nf-library" data-library-field>
        <input type="text" class="nf-library-id" name="folder-__I__-library_id"
               placeholder="—" aria-label="Library id">
        <input type="hidden" name="folder-__I__-library_name" value="">
        <button type="button" class="nf-fetch-lib" data-fetch-lib>Fetch</button>
      </div>
```

After the existing `{% include "_folder_picker.html" %}` line, add:

```html
  {% include "_library_picker.html" %}
```

- [ ] **Step 5: Create the picker dialog shell**

Create `mediascanmonitor/web/templates/_library_picker.html`. **Reuse the existing
`.fs-dialog*` chrome classes** (already fully themed with the project's design tokens in
`app.css` — `.fs-dialog`, `.fs-dialog-head`, `.fs-close`, `.fs-dialog-foot`, `.fs-cancel`,
`.fs-select`) so no dialog-shell CSS is duplicated. The JS keys off the `data-library-*`
attributes, independent of these styling classes:

```html
{# Library picker dialog — one shared instance per page (one folder editor per page, same
   assumption as _folder_picker.html). Reuses the .fs-dialog* chrome. Opened per row by the
   library-picker JS in _folder_rows_script.html, which POSTs the server form to the row's
   data-library-endpoint, swaps options into #library-listing, and writes the chosen id + name
   back on Select. #}
<dialog class="fs-dialog" data-library-picker aria-label="Choose a library">
  <div class="fs-dialog-head">
    <h2>Choose a library</h2>
    <button type="button" class="fs-close" data-library-close aria-label="Close">&times;</button>
  </div>
  <div id="library-listing" class="lib-listing"></div>
  <div class="fs-dialog-foot">
    <button type="button" class="fs-cancel" data-library-close>Cancel</button>
    <button type="button" class="fs-select" data-library-pick disabled>Select</button>
  </div>
</dialog>
```

- [ ] **Step 6: Wire the picker JS**

In `mediascanmonitor/web/templates/_folder_rows_script.html`, add a new `<script>` block at the end (after the extension-chip block). It mirrors the folder-picker block's structure (shared dialog, event delegation, `.lib-picker-ready` reveal, `htmx.ajax` with `source` to include the enclosing form).

> **Riskiest line in the feature:** `htmx.ajax("POST", url, {source: btn})` relies on htmx
> collecting the parameters from `source`'s enclosing `<form>` — the existing folder Browse
> picker uses a query-string GET with no `source`, so there's no in-repo precedent. htmx does
> document this behavior, but the **manual verification step is the gate for it** — if the POST
> arrives with no `type`/`base_url`/`secret`, that's the cause. (Fallback if it misbehaves: pass
> the fields explicitly via the `values` option built from the form.)

```html
<script>
  // Library picker: per-row Fetch posts the server form to the row's data-library-endpoint
  // (unsaved /ui/servers/libraries on the new page, /ui/servers/{id}/libraries on detail),
  // swaps options into the shared dialog, and writes the chosen id + name back on Select.
  // Progressive enhancement: adds .lib-picker-ready so the CSS-hidden Fetch buttons appear
  // only when JS is present AND the editor's type supports discovery. No JS → free-text id.
  (function () {
    const dialog = document.querySelector("[data-library-picker]");
    const listing = document.getElementById("library-listing");
    if (!dialog || !listing || typeof dialog.showModal !== "function") return;

    const pick = dialog.querySelector("[data-library-pick]");
    let targetRow = null;

    for (const editor of document.querySelectorAll("[data-folder-editor]")) {
      editor.classList.add("lib-picker-ready"); // reveals Fetch (gated also on data-library-discovery)
      editor.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-fetch-lib]");
        if (!btn) return;
        targetRow = btn.closest("[data-folder-row]");
        pick.disabled = true;
        listing.innerHTML = '<p class="muted">Loading…</p>';
        dialog.showModal();
        htmx.ajax("POST", editor.dataset.libraryEndpoint, {
          target: "#library-listing",
          swap: "innerHTML",
          source: btn, // includes the enclosing server form's fields (type, base_url, secret, …)
        });
      });
    }

    // Enable Select only once a library radio is chosen.
    listing.addEventListener("change", () => {
      pick.disabled = !listing.querySelector('input[name="lib-choice"]:checked');
    });

    dialog.addEventListener("click", (event) => {
      if (event.target === dialog || event.target.closest("[data-library-close]")) {
        dialog.close();
        return;
      }
      if (event.target.closest("[data-library-pick]")) {
        const chosen = listing.querySelector('input[name="lib-choice"]:checked');
        if (chosen && targetRow) {
          targetRow.querySelector('input[name$="-library_id"]').value = chosen.value;
          const nameInput = targetRow.querySelector('input[name$="-library_name"]');
          if (nameInput) nameInput.value = chosen.dataset.libName || "";
          dialog.close();
        }
      }
    });
  })();
</script>
```

- [ ] **Step 7: Toggle the flag in the new-server `apply()` JS**

In `mediascanmonitor/web/templates/server_new.html`, inside the `apply()` IIFE, capture the editor once and set its flag each time `apply()` runs. Add after `const modeSel = ...`:

```javascript
    const editor = form.querySelector("[data-folder-editor]");
```

and inside `function apply() { ... }`, after the existing field toggles, add:

```javascript
      if (editor) {
        editor.dataset.libraryDiscovery = spec.supports_library_discovery ? "true" : "false";
      }
```

- [ ] **Step 8: Style the Fetch button, name label, and option list**

Two edits to `mediascanmonitor/web/static/app.css`. The picker dialog needs **no new chrome
CSS** — `_library_picker.html` reuses `.fs-dialog*` (Step 5) — only the library cell layout and
the option-list content.

**(a) Make the wrapped library cell the grid child.** The current rule targets the bare input
as a *direct* child, which breaks once it's wrapped in `.nf-library`. Change it to target the
wrapper by class — exactly how `.nf-path`/`.nf-ext` already claim their grid areas:

```css
/* was: .nf-row > input[name$="-library_id"] { grid-area: lib; } */
.nf-row .nf-library { grid-area: lib; display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: center; }
.nf-library-id { flex: 1 1 auto; min-width: 0; }
.nf-library-name { flex: 1 0 100%; }  /* friendly name wraps to its own line under the id */
```

And in the existing `@media (max-width: 680px)` block, replace the bare-input rule
`.nf-row input[name$="-library_id"] { flex: 1 1 16rem; }` with the wrapper:

```css
  .nf-row .nf-library { flex: 1 1 16rem; }
```

**(b) Append the Fetch-button + option-list rules** (reusing palette tokens + the `.fs-error`
style; no hardcoded colours):

```css
/* Library discovery: the per-row Fetch button + picker option list. Fetch is hidden unless JS
   is present (.lib-picker-ready) AND the server type supports discovery — so a no-JS page keeps
   the plain free-text library id with no dead control. Dialog chrome reuses .fs-dialog*. */
.nf-fetch-lib {
  flex: 0 0 auto;
  display: none;
  font: 600 0.78rem var(--sans);
  padding: 0.34rem 0.55rem;
  color: var(--signal); background: transparent;
  border: 1px solid var(--signal-dim); border-radius: var(--r-sm);
}
.nf-fetch-lib:hover { background: var(--signal); color: #04181b; border-color: var(--signal); }
.folder-editor.lib-picker-ready[data-library-discovery="true"] .nf-fetch-lib { display: inline-flex; }
.nf-library-name { font: 0.78rem/1.4 var(--sans); color: var(--muted); }

.lib-listing { display: flex; flex-direction: column; min-height: 0; }
.lib-options { list-style: none; margin: 0; padding: 0 0.5rem; overflow: auto; max-height: 46vh; }
.lib-option {
  display: flex; align-items: baseline; gap: 0.55rem;
  width: 100%; padding: 0.5rem 0.45rem;
  border-bottom: 1px solid var(--line-2); cursor: pointer;
}
.lib-options li:last-child .lib-option { border-bottom: 0; }
.lib-option:hover { background: var(--panel-2); }
.lib-name { font-weight: 600; }
.lib-id { font: 0.82rem var(--mono); }
.lib-empty, .lib-error { font: 0.82rem/1.45 var(--sans); padding: 0.7rem 1rem; margin: 0; }
.lib-empty { color: var(--muted); }
.lib-error { color: #f6b9bd; }  /* same as .fs-error */
```

> The `#04181b`, `--signal-dim`, `#f6b9bd` values are copied from the existing `.nf-browse` /
> `.fs-select` / `.fs-error` rules so the Fetch button and option list read as the same
> component family as the folder picker. Confirm the token names against `app.css` before
> pasting (they're defined in the `:root` block).

- [ ] **Step 9: Reconcile the FOLLOWUPS entry**

In `docs/FOLLOWUPS.md`, **replace** the satisfied item (currently):

```
- [ ] `library_id` discovery dropdowns (needs a `ServerAdapter.list_libraries()` on the frozen ABC);
      the UI ships **free-text** `library_id` for now. → phase3 README decision 3
```

with:

```
- [ ] `library_id` discovery for **Plex / Emby / Jellyfin** — the general capability + picker UI
      shipped for Audiobookshelf (2026-06-24-library-discovery). Each remaining backend = flip
      `supports_library_discovery` + implement `list_libraries()` (Plex `GET /library/sections`
      → `Directory[].{key,title}`; Emby/Jellyfin `GET /Library/VirtualFolders` → `{Name,ItemId}`;
      verify at implement-time). → docs/superpowers/specs/2026-06-23-library-discovery-design.md
```

Then check the README for a now-stale "free-text only" claim and fix if present:

Run: `grep -ni "free-text\|library_id\|decision 3" README.md`
If a line claims library ids are free-text-only, update it to note ABS now offers a picker; if nothing matches, leave the README unchanged.

- [ ] **Step 10: Run the tests to confirm they pass**

Run: `pytest tests/web/test_pages.py -k "discovery or library" -v`
Expected: PASS.

- [ ] **Step 11: Run the full gate and commit**

```bash
ruff format . && ruff check . && mypy mediascanmonitor && pytest
git add mediascanmonitor/web/pages.py mediascanmonitor/web/templates/_folder_editor.html mediascanmonitor/web/templates/_library_picker.html mediascanmonitor/web/templates/_folder_rows_script.html mediascanmonitor/web/templates/server_new.html mediascanmonitor/web/static/app.css docs/FOLLOWUPS.md tests/web/test_pages.py
git commit -m "feat(web): per-row Fetch → library picker dialog (ABS), with no-JS fallback"
```

---

## Manual verification (after Task 4)

Spin up the dev server and click through the real UI (the unit/integration tests mock the
backend; this confirms the JS wiring end-to-end):

```bash
scripts/dev_serve.sh   # http://0.0.0.0:8099, password dev
```

1. `/servers/new` → select **Audiobookshelf** → the **Fetch** button appears next to Library
   (it is hidden for webhook/plex defaults). Switch type back to webhook → it disappears.
2. Fill a real ABS base URL + token → click **Fetch** → the dialog lists libraries → pick one
   → the row's Library id fills in.
3. Save → reopen the server detail page → the friendly name shows under the id; **Fetch**
   re-fetches using the stored token without re-entering it.
4. Disable JS → the Library field is a plain text input, no Fetch button — manual entry works.

---

## Self-Review (completed during planning)

- **Spec coverage:** Component 1 → Task 1; Component 2 → Task 2; Component 3 → Task 3;
  Component 4 → Task 4; Error-handling table → Task 1 (adapter) + Task 3 (endpoint/helper);
  Testing pyramid → split across Tasks 1–4; Scope/FOLLOWUPS → Task 4 Step 9. No gaps.
- **Type consistency:** `LibraryOption`/`LibraryListResult`/`supports_library_discovery`/
  `list_libraries`/`run_library_listing`/`library_name` are spelled identically everywhere
  they appear across tasks.
- **No placeholders:** every code step shows complete code; every run step has an exact
  command + expected result.
- **Consolidation (post-Opus-review):** the listing helper lives in `serverprobe.py` (renamed
  from `servertest.py`, not a new module) and shares one `_with_adapter` client-lifecycle
  primitive with `run_connectivity_test`;
  the picker dialog reuses the existing `.fs-dialog*` chrome (no duplicated dialog CSS); the
  library grid cell claims its area by class like `.nf-path`/`.nf-ext` (not a direct-child
  selector). The two endpoints + two render helpers stay deliberately parallel to the existing
  twin Test endpoints — that mirror is the house pattern, not redundancy.
