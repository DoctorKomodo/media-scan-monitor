# Phase 3 — Sub-plan 04: Dashboard, htmx UI & SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking. Read the **frozen contract**
> ([`2026-06-20-phase3-00-web-interface-contract.md`](2026-06-20-phase3-00-web-interface-contract.md))
> §K before starting — this sub-plan **owns** §K and **consumes** the rest.

**Goal:** Build the browser UI on top of the JSON API (02) and the engine/status seam (03): server-rendered
Jinja2 pages behind the password (`require_page_auth`), htmx `/ui/...` HTML-partial form handlers that
mutate config through the **shared write-cores** from `web/writes.py`, a live event feed backed by a plain
`StreamingResponse` SSE endpoint over `EventsBus`, and the vendored static assets. This sub-plan lands
**last** in Phase 3 (it depends on 02's write-cores + read-schemas and 03's `/api/status` + gate routes).

**Architecture:** One new route module `web/pages.py` holds the page routes (`GET /`, `/servers`,
`/servers/{id}`, `/settings`, `/events`), the htmx `/ui/...` form handlers, and the SSE
`GET /events/stream`. All of them sit on **one** `APIRouter` guarded at router level by
`require_page_auth` (contract §B). The `/ui/*` mutations are thin: parse `Form(...)`, build the existing
write-schemas (`ServerCreate`/`ServerUpdate`/`FolderCreate`/`FolderUpdate`), and call the **shared
write-cores** (`apply_server_*`/`apply_folder_*`, contract §J) so the browser path validates, writes
off-thread, and `rebuild_engine`s **identically** to `/api/*` — there is no second validation/rebuild
codepath. Type-specific form fields come from `SERVER_TYPE_SPECS` + `supported_scan_modes` (contract §D),
never from a literal type name (invariant 6). The SSE endpoint replays `bus.recent()` then yields
`bus.subscribe()` frames; no `sse-starlette`. Static assets (vendored `htmx.min.js`, `app.css`) mount at
`/static`.

**Tech Stack:** Python 3.14, FastAPI + Starlette (already pinned by 01), `jinja2==3.1.6` (already pinned),
`python-multipart` (form parsing, added by 01), htmx (vendored static file, pinned at implement-time),
`pytest==9.1.0` + `pytest-asyncio==1.4.0`, `httpx` (`TestClient` + async `AsyncClient` for SSE).
`ruff`/`mypy --strict` clean, line length 100. PEP 649 annotations — **no** `from __future__ import annotations`.

---

## Global Constraints

Binding rules copied from CLAUDE.md + the Phase 3 contract; every task honors all of them.

- **PEP 649:** never add `from __future__ import annotations`; leave forward refs unquoted. A name used
  in a runtime-introspected annotation must be importable at runtime (not behind `TYPE_CHECKING`).
- **`StrEnum`, never `(str, Enum)`.** The enums (`ServerType`, `ScanMode`, `DebounceMode`, `EngineState`)
  are the existing `StrEnum`s; `str(member)` / `member.value` is the bare value. Iterate `ServerType` for
  type lists; do not hardcode the member names.
- **ruff `select` is exactly `E, F, I, UP, B, C4, SIM, RUF`** (per-file-ignore `B` under `tests/**`). No
  `# noqa` for any other rule (trips `RUF100`). `# noqa: F401` on a self-registration import is valid.
- **`mediascanmonitor` is first-party for isort** — separate third-party from first-party imports with a
  blank line (`I001` is autofixable).
- **`mypy --strict` clean**; full type hints; line length 100.
- **Off-loop I/O (invariant 3):** every `Repo` call from a coroutine goes through
  `await asyncio.to_thread(...)`. Never call a `Repo` method directly in an `async def`. The write-cores
  (02) already do this internally; this sub-plan's direct repo reads (status counts, `get_setting`) use
  `asyncio.to_thread`. `check_watch_limit` is the engine's job — pages read `engine.watch_limit`, they do
  not re-measure.
- **No secret in any template, SSE record, URL, or log line (invariant 1 / CLAUDE rule 5).** Pages render
  `ServerRead`/`StatusRead`/`EventRecord` (all redacted: only `has_secret: bool`, never the token or
  ciphertext). The secret `<input>` is **write-only** — its value is never echoed back into the edit form.
- **No server-type literal branching (invariant 6).** Per-type form behavior (secret-required, base-url-
  required, webhook fields, valid scan-modes) is driven entirely by `SERVER_TYPE_SPECS` (§D) +
  `registry.get_adapter_class(type).supported_scan_modes`. Templates/handlers/JS never test
  `type == "plex"` (or any literal member); they consume the serialized spec/scan-mode maps.
- **Every `/ui` write goes through the shared write-core (invariant 4 / §K).** Each `/ui/*` mutation calls
  the matching `apply_server_*`/`apply_folder_*` from `web/writes.py` (02), so it performs the §D
  token-required `422` check, the off-thread repo write, and `rebuild_engine` — identical to `/api/*`. The
  `/ui` handlers only differ in input parsing (`Form` vs JSON) and output shape (HTML partial vs Pydantic).
- **Auth-closed (invariant 2):** the pages router is guarded by `require_page_auth` at router level. The
  only unauthenticated routes are 01's `GET /login` / `GET /setup` (not redefined here) and `/static/*`
  (StaticFiles is on the §B allow-list). Adding a route = deciding its guard; everything here is guarded.
- **SSE without `sse-starlette`:** a plain `StreamingResponse(media_type="text/event-stream")` over an
  async generator. **Known race (acceptable, do not lock):** a record published between the `recent()`
  snapshot and the `subscribe()` registration may be missed or shown twice — fine for a best-effort feed.
- **Trusted server-side templates:** these Jinja2 templates are authored by us and rendered via
  `app.state.templates` (autoescaping HTML). This is **distinct** from the webhook adapter's
  user-supplied templates (which use a `SandboxedEnvironment`); do **not** import or apply the sandbox here.

Verification gate (Task 6): `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest` green.

---

## Consumed interfaces (frozen — match EXACTLY; do not invent)

From **01** (`web/app.py`, `web/deps.py`, `observ/events_bus.py`, templates):
- `create_app(repo, engine, events_bus, *, session_secret) -> FastAPI`. `app.state.repo`,
  `app.state.engine`, `app.state.events_bus`, `app.state.templates` (`Jinja2Templates`, dir
  `web/templates`). This sub-plan **edits `create_app`** to `include_router(pages_router)` and `app.mount`
  the static dir (the shared merge points — keep every existing `include_router` line).
- `require_page_auth(request) -> None` (raises `HTTPException(303, headers={"Location": "/login"})`
  when unauthenticated, `/setup` when no password is set). `get_repo`/`get_engine`/`get_events_bus`/
  `get_templates` accessors (`web/deps.py`).
- `templates/base.html` — extended by every page here; provides `{% block title %}` and
  `{% block content %}` and loads `/static/app.css` + `/static/htmx.min.js` in its `<head>`. (The static
  files + mount are supplied by **this** sub-plan; base.html references them in anticipation.)
- `EventsBus.recent(limit=50) -> list[EventRecord]`, `EventsBus.subscribe() -> AsyncIterator[EventRecord]`.
  `EventRecord` is a frozen slotted dataclass with fields: `ts, server_id, server_name, scan_mode,
  scan_key, scan_path, library_id, event_type, file_path, ok, status_code, detail` (no secret field).

From **02** (`web/writes.py`, `web/api_schemas.py`):
- Shared write-cores (each `async`, off-thread repo write + §D token check + `rebuild_engine`, raising
  `fastapi.HTTPException(422)` on the token-required violation):
  ```python
  async def apply_server_create(repo: Repo, engine: Engine, data: ServerCreate) -> Server: ...
  async def apply_server_update(repo: Repo, engine: Engine, server_id: int, data: ServerUpdate) -> Server: ...
  async def apply_server_delete(repo: Repo, engine: Engine, server_id: int) -> None: ...
  async def apply_folder_create(repo: Repo, engine: Engine, server_id: int, data: FolderCreate) -> Folder: ...
  async def apply_folder_update(repo: Repo, engine: Engine, folder_id: int, data: FolderUpdate) -> Folder: ...
  async def apply_folder_delete(repo: Repo, engine: Engine, folder_id: int) -> None: ...
  ```
- `SERVER_TYPE_SPECS: dict[ServerType, ServerTypeSpec]` (`requires_secret`, `requires_base_url`,
  `is_webhook`); `ServerRead.from_model(server, folders) -> ServerRead` (redacted; has
  `has_secret`, `supported_scan_modes: list[ScanMode]`, the `webhook_*` fields, `folders: list[FolderRead]`);
  `FolderRead.from_model(folder) -> FolderRead`.

From **03** (`web/api/system.py`):
- `StatusRead` (`engine_state`, `inotify_gate`, `watch_current`, `watch_dirs`, `watch_needed`,
  `watch_recommended`, `watch_ok`, `server_count`, `enabled_server_count`).
- `PUT /api/settings/inotify-gate` (body `{"inotify_gate": "enforce"|"off"}`) and `POST /api/engine/recheck`
  — both `set_setting`/`rebuild_engine` semantics. This sub-plan's `/ui/settings` + `/ui/recheck` are the
  **HTML twins** of these (same `set_setting` + `rebuild_engine` path, HTML partial out — see §H note).

From **Phase 1** (consumed read-only): `Repo` (`db/repo.py`); `Server`/`Folder`/`ServerType`/`ScanMode`/
`DebounceMode` (`db/models.py`); `ServerCreate`/`ServerUpdate`/`FolderCreate`/`FolderUpdate` (`db/schemas.py`);
`registry.get_adapter_class` (`servers/registry.py`); `Engine`/`EngineState` + `engine.state` /
`engine.watch_limit: WatchLimitStatus | None` (`engine.py`); `WatchLimitStatus`
(`current, dirs, needed, recommended, ok`, `watcher/watch_limit.py`).

---

## Test harness (from sub-plan 01 — ASSUME these exist; do not recreate)

`tests/web/conftest.py` (created by 01) provides:
- `repo` — a real `Repo` over an in-memory/temp SQLite DB with a test `SecretBox`.
- `events_bus` — a real `EventsBus()` (so `recent()`/`subscribe()`/`publish()` behave).
- `engine` — a **FakeEngine** stub with mutable `.state: EngineState`, mutable
  `.watch_limit: WatchLimitStatus | None`, and an `async def rebuild()` that increments a call counter
  (`engine.rebuild_calls: int`). It satisfies the `Engine` type where the write-cores/`rebuild_engine`
  touch it. (Tests in this sub-plan set `engine.state` / `engine.watch_limit` as needed.)
- `app` — `create_app(repo, engine, events_bus, session_secret="test-secret")`.
- `client` — Starlette `TestClient(app)`, **unauthenticated** (no session). Use for redirect assertions
  (`follow_redirects=False`).
- `auth_client` — `TestClient(app)` with a logged-in session (`session["authed"] = True`). Use for page +
  `/ui` form posts.
- `aclient` — async `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")` with a
  logged-in session cookie. Use for the async SSE stream read.

If a fixture name differs once 01 lands, adjust the imports — the contract froze the behaviors, not the
local fixture spellings. New test files live under `tests/web/`.

> **PATH note (state once):** all `pytest` / `ruff` / `mypy` commands below run after
> `export PATH="$PWD/.venv/bin:$PATH"` (the venv bin is not on `PATH` by default). After that, invoke the
> bare tools (`pytest …`, `ruff …`, `mypy …`).

---

## File Structure (what this sub-plan builds)

| File | Action | Responsibility |
|------|--------|----------------|
| `mediascanmonitor/web/pages.py` | Create | Pages router: page GETs, `/ui/*` form handlers, `/events/stream` SSE, `_status_context` helper. |
| `mediascanmonitor/web/app.py` | Modify | `include_router(pages_router)` + `app.mount("/static", StaticFiles(...))`. |
| `mediascanmonitor/web/templates/_nav.html` | Create | Shared nav partial included by every page. |
| `mediascanmonitor/web/templates/dashboard.html` | Create | `GET /` — engine state + watch gate + server/health summary. |
| `mediascanmonitor/web/templates/servers.html` | Create | `GET /servers` — server list + type-driven add form. |
| `mediascanmonitor/web/templates/server_detail.html` | Create | `GET /servers/{id}` — edit form, folders, Test button. |
| `mediascanmonitor/web/templates/settings.html` | Create | `GET /settings` — inotify gate toggle + re-check. |
| `mediascanmonitor/web/templates/events.html` | Create | `GET /events` — live feed page opening the SSE stream. |
| `mediascanmonitor/web/templates/_servers_list.html` | Create | Server-list partial (swap target after create/delete). |
| `mediascanmonitor/web/templates/_folders.html` | Create | Folder-list partial (swap target after folder writes). |
| `mediascanmonitor/web/templates/_status.html` | Create | Status panel partial (swap target for settings/recheck). |
| `mediascanmonitor/web/templates/_error.html` | Create | Inline error partial (rendered on the §D 422). |
| `mediascanmonitor/web/static/app.css` | Create | Minimal dashboard stylesheet. |
| `mediascanmonitor/web/static/htmx.min.js` | Create | Vendored htmx (pinned at implement-time — see Task 5). |
| `tests/web/test_sse.py` | Create | SSE replay/stream + unauth redirect. |
| `tests/web/test_pages.py` | Create | Page GETs (200 authed / 303 unauth / key content). |
| `tests/web/test_ui_forms.py` | Create | `/ui` server+folder CRUD: success swap, 422 inline error, rebuild invoked. |
| `tests/web/test_ui_settings.py` | Create | `/ui/settings` + `/ui/recheck`: persists + rebuild ran. |
| `tests/web/test_static.py` | Create | `/static/app.css` 200 smoke. |

---

### Task 1: SSE endpoint `GET /events/stream` + pages router skeleton

**Files:**
- Create: `tests/web/test_sse.py`
- Create: `mediascanmonitor/web/pages.py`
- Modify: `mediascanmonitor/web/app.py`

**Interfaces:**
- Consumes: `require_page_auth`, `get_events_bus` (`web/deps.py`); `EventsBus.recent`/`.subscribe`,
  `EventRecord` (`observ/events_bus.py`); `Request`, `APIRouter`, `Depends` (FastAPI);
  `StreamingResponse` (Starlette/FastAPI).
- Produces: `router: APIRouter` (guarded by `require_page_auth`) with `GET /events/stream`;
  `create_app` now mounts it.

- [ ] **Step 1: Write the failing SSE test**

Create `tests/web/test_sse.py`:

```python
"""SSE /events/stream: auth-guarded, replays recent records as text/event-stream frames."""

import json

import httpx

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus


def _make_record(server_name: str = "Plex Main") -> EventRecord:
    return EventRecord(
        ts="2026-06-20T18:30:00+00:00",
        server_id=1,
        server_name=server_name,
        scan_mode="targeted",
        scan_key="plex:1:/data/tv/Show",
        scan_path="/data/tv/Show",
        library_id="2",
        event_type="created",
        file_path="/data/tv/Show/ep01.mkv",
        ok=True,
        status_code=200,
        detail="scan queued",
    )


def test_events_stream_requires_auth(client: httpx.Client) -> None:
    resp = client.get("/events/stream", follow_redirects=False)
    assert resp.status_code == 303
    # The `client`/`app` fixtures set no password, so require_page_auth sends an anonymous user to
    # first-run setup (it only targets /login once a password exists — see 01's require_page_auth).
    assert resp.headers["location"] == "/setup"


async def test_events_stream_replays_recent_records(
    aclient: httpx.AsyncClient, events_bus: EventsBus
) -> None:
    # Publish BEFORE connecting so recent() has it -> deterministic, dodges the replay/subscribe race.
    events_bus.publish(_make_record("Replayed Server"))

    found: dict[str, object] | None = None
    async with aclient.stream("GET", "/events/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                found = json.loads(line.removeprefix("data: "))
                break  # got the replayed frame; closing the stream cancels the generator

    assert found is not None
    assert found["server_name"] == "Replayed Server"
    assert "secret" not in found  # invariant 1: no token field ever in an SSE record
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/web/test_sse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.pages'` (and `create_app`
does not mount the route yet).

- [ ] **Step 3: Create `web/pages.py` with the router + SSE endpoint**

Create `mediascanmonitor/web/pages.py`:

```python
"""Browser UI: server-rendered pages, htmx /ui form handlers, and the SSE event stream.

All routes are guarded by ``require_page_auth`` at router level (contract §B): unauthenticated
requests get a 303 redirect to /login (or /setup when no password is set). The only
unauthenticated web surface — /login, /setup, /static/* — is owned elsewhere (01 / StaticFiles).

The /ui/* mutations are thin presentations of the SAME write as /api/*: they parse ``Form(...)``,
build the existing write-schemas, and call the shared write-cores in ``web/writes.py`` (contract §J),
so they validate (incl. the §D token-required 422), write off-thread, and ``rebuild_engine``
identically to the JSON API. They differ only in input parsing and HTML-partial output (invariant 4).

SSE (contract §K): a plain ``StreamingResponse(media_type="text/event-stream")`` over an async
generator that replays ``bus.recent()`` then yields ``bus.subscribe()`` frames as ``data: {json}\\n\\n``,
breaking on ``await request.is_disconnected()``. No sse-starlette. Known, accepted race: a record
published between the recent() snapshot and subscribe() registration may be missed or duplicated.
"""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.servers import registry
from mediascanmonitor.web.deps import (
    get_engine,
    get_events_bus,
    get_repo,
    get_templates,
    require_page_auth,
)

router = APIRouter(dependencies=[Depends(require_page_auth)])


def _sse_frame(record: EventRecord) -> str:
    """Serialize a (secret-free) EventRecord as one SSE ``data:`` frame."""
    return f"data: {json.dumps(dataclasses.asdict(record))}\n\n"


async def _event_generator(request: Request, bus: EventsBus) -> AsyncIterator[str]:
    for record in bus.recent():
        yield _sse_frame(record)
    async for record in bus.subscribe():
        if await request.is_disconnected():
            break
        yield _sse_frame(record)


@router.get("/events/stream")
async def events_stream(
    request: Request, bus: EventsBus = Depends(get_events_bus)
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(request, bus),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

> The unused imports (`asyncio`, `ServerType`, `registry`, `get_engine`, `get_repo`, `get_templates`)
> are added now because Tasks 2–4 use them; if `ruff` flags `F401` at this step, leave them out and
> re-add per task. To keep the build green at every commit, the minimal-but-complete approach is to add
> each import in the task that first uses it. For Task 1 keep ONLY: `dataclasses`, `json`,
> `AsyncIterator`, `APIRouter`, `Depends`, `Request`, `StreamingResponse`, `EventRecord`, `EventsBus`,
> `get_events_bus`, `require_page_auth`.

Use exactly this Task-1 import block in `web/pages.py`:

```python
import dataclasses
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.web.deps import get_events_bus, require_page_auth
```

- [ ] **Step 4: Mount the pages router in `create_app`**

Modify `mediascanmonitor/web/app.py`. Add the import near the other router imports:

```python
from mediascanmonitor.web.pages import router as pages_router
```

and, alongside the existing `app.include_router(...)` lines inside `create_app` (keep every existing
line — this is the shared merge point), add:

```python
    app.include_router(pages_router)
```

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/web/test_sse.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/pages.py mediascanmonitor/web/app.py tests/web/test_sse.py && mypy mediascanmonitor/web/pages.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/pages.py mediascanmonitor/web/app.py tests/web/test_sse.py
git commit -m "feat(web): SSE /events/stream over EventsBus + pages router skeleton"
```

---

### Task 2: Page routes + templates (dashboard, servers, detail, settings, events)

**Files:**
- Create: `tests/web/test_pages.py`
- Modify: `mediascanmonitor/web/pages.py`
- Create: `mediascanmonitor/web/templates/_nav.html`, `dashboard.html`, `servers.html`,
  `server_detail.html`, `settings.html`, `events.html`

**Interfaces:**
- Consumes: `get_repo`/`get_engine`/`get_templates` (`web/deps.py`); `Repo.list_servers`/`.get_server`/
  `.list_folders`/`.get_setting` (`db/repo.py`, off-thread); `ServerRead.from_model`,
  `SERVER_TYPE_SPECS` (`web/api_schemas.py`); `registry.get_adapter_class` (`servers/registry.py`);
  `ServerType`/`ScanMode`/`DebounceMode` (`db/models.py`); `engine.state`/`engine.watch_limit`.
- Produces: page GETs `/`, `/servers`, `/servers/{id}`, `/settings`, `/events`; a `_status_context`
  helper (reused by Task 4).

- [ ] **Step 1: Write the failing page tests**

Create `tests/web/test_pages.py`:

```python
"""Page routes: 200 for an authed client, 303 redirect for an anon client, key content present."""

import httpx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate
from mediascanmonitor.engine import EngineState
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus


def _seed_server(repo) -> int:  # type: ignore[no-untyped-def]
    server = repo.create_server(
        ServerCreate(name="Plex Main", type=ServerType.plex, base_url="http://plex:32400", secret="tok")
    )
    repo.create_folder(server.id, FolderCreate(path="/data/tv", library_id="2", extensions=["mkv"]))
    return int(server.id)


def test_dashboard_redirects_when_anon(client: httpx.Client) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    # No password set on the `client` fixture → require_page_auth routes to first-run /setup
    # (it targets /login only once a password exists).
    assert resp.headers["location"] == "/setup"


def test_dashboard_renders_engine_and_watch_status(auth_client: httpx.Client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.state = EngineState.blocked
    engine.watch_limit = WatchLimitStatus(current=8192, dirs=20000, needed=24000, recommended=28800, ok=False)
    resp = auth_client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "blocked" in body
    assert "28800" in body  # recommended kernel ceiling surfaced
    assert "max_user_watches" in body  # the recommended sysctl line


def test_servers_page_lists_servers_and_add_form(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    _seed_server(repo)
    resp = auth_client.get("/servers")
    assert resp.status_code == 200
    assert "Plex Main" in resp.text
    assert 'name="type"' in resp.text  # the add-server form is present


def test_server_detail_shows_folders_and_test_button(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    sid = _seed_server(repo)
    resp = auth_client.get(f"/servers/{sid}")
    assert resp.status_code == 200
    assert "/data/tv" in resp.text
    assert "Test" in resp.text  # Test button present


def test_server_detail_404_for_missing(auth_client: httpx.Client) -> None:
    assert auth_client.get("/servers/9999").status_code == 404


def test_settings_page_has_gate_toggle(auth_client: httpx.Client) -> None:
    resp = auth_client.get("/settings")
    assert resp.status_code == 200
    assert 'name="inotify_gate"' in resp.text
    assert "Re-check" in resp.text


def test_events_page_opens_stream(auth_client: httpx.Client) -> None:
    resp = auth_client.get("/events")
    assert resp.status_code == 200
    assert "/events/stream" in resp.text  # the page wires the SSE source


def test_pages_redirect_when_anon(client: httpx.Client) -> None:
    for path in ("/servers", "/settings", "/events"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/web/test_pages.py -v`
Expected: FAIL — routes 404 / templates missing.

- [ ] **Step 3: Add the page routes + `_status_context` to `web/pages.py`**

Replace the Task-1 import block in `web/pages.py` with this fuller block (additive — keeps Task-1 names):

```python
import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.servers import registry
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS, ServerRead
from mediascanmonitor.web.deps import (
    get_engine,
    get_events_bus,
    get_repo,
    get_templates,
    require_page_auth,
)
```

Then, after the SSE route, add the status helper and the page routes:

```python
def _scan_modes_by_type() -> dict[str, list[str]]:
    """{type value: sorted supported scan-mode values} — drives the add-form, no type literals (inv 6)."""
    return {
        server_type.value: sorted(
            mode.value for mode in registry.get_adapter_class(server_type).supported_scan_modes
        )
        for server_type in ServerType
    }


def _type_specs() -> dict[str, dict[str, bool]]:
    """Serialize SERVER_TYPE_SPECS for the template/JS (the one place per-type rules live, §D)."""
    return {
        server_type.value: {
            "requires_secret": spec.requires_secret,
            "requires_base_url": spec.requires_base_url,
            "is_webhook": spec.is_webhook,
        }
        for server_type, spec in SERVER_TYPE_SPECS.items()
    }


async def _status_context(repo: Repo, engine: Engine) -> dict[str, Any]:
    """Shared dashboard/settings status context — same primitives as /api/status (§H)."""
    gate = await asyncio.to_thread(repo.get_setting, "inotify_gate")
    servers = await asyncio.to_thread(repo.list_servers)
    limit = engine.watch_limit
    return {
        "engine_state": engine.state.value,
        "inotify_gate": gate or "enforce",
        "watch": limit,  # WatchLimitStatus | None (.current/.dirs/.needed/.recommended/.ok)
        "server_count": len(servers),
        "enabled_server_count": sum(1 for s in servers if s.enabled),
    }


@router.get("/")
async def dashboard(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@router.get("/servers")
async def servers_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    servers = await asyncio.to_thread(repo.list_servers)
    return templates.TemplateResponse(
        request=request,
        name="servers.html",
        context={
            "servers": servers,
            "server_types": [t.value for t in ServerType],
            "scan_modes": [m.value for m in ScanMode],
            "debounce_modes": [m.value for m in DebounceMode],
            "type_specs": _type_specs(),
            "scan_modes_by_type": _scan_modes_by_type(),
        },
    )


async def _load_server_read(repo: Repo, server_id: int) -> ServerRead:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"server {server_id} not found")
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return ServerRead.from_model(server, folders)


@router.get("/servers/{server_id}")
async def server_detail(
    request: Request,
    server_id: int,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    server = await _load_server_read(repo, server_id)
    return templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={
            "server": server,
            "scan_modes": [m.value for m in ScanMode],
            "debounce_modes": [m.value for m in DebounceMode],
        },
    )


@router.get("/settings")
async def settings_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="settings.html", context=context)


@router.get("/events")
async def events_page(
    request: Request, templates: Jinja2Templates = Depends(get_templates)
) -> Response:
    return templates.TemplateResponse(request=request, name="events.html", context={})
```

> `get_engine`, `get_repo`, `get_templates`, `HTTPException`, `Jinja2Templates`, `Response`,
> `DebounceMode`, `ScanMode`, `ServerType`, `registry`, `SERVER_TYPE_SPECS`, `ServerRead`, `Repo`,
> `Engine`, `asyncio`, `Any` are all now used. `EventRecord`/`EventsBus`/`StreamingResponse`/`dataclasses`/
> `json`/`AsyncIterator` remain used by the SSE route from Task 1.

- [ ] **Step 4: Create the templates**

Create `mediascanmonitor/web/templates/_nav.html`:

```html
<nav class="nav">
  <a href="/">Dashboard</a>
  <a href="/servers">Servers</a>
  <a href="/settings">Settings</a>
  <a href="/events">Events</a>
  <form action="/auth/logout" method="post" class="nav-logout">
    <button type="submit">Log out</button>
  </form>
</nav>
```

Create `mediascanmonitor/web/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<h1>Dashboard</h1>

<section class="card">
  <h2>Engine</h2>
  <p>State: <span class="state state-{{ engine_state }}">{{ engine_state }}</span></p>
  <p>Inotify gate: <strong>{{ inotify_gate }}</strong></p>
  <p>Servers: {{ enabled_server_count }} enabled / {{ server_count }} total</p>
</section>

<section class="card">
  <h2>inotify watch limit</h2>
  {% if watch is none %}
    <p>No watches configured yet — nothing to gate.</p>
  {% else %}
    <p>Current kernel limit: {{ watch.current }}</p>
    <p>Directories watched: {{ watch.dirs }} &middot; Needed: {{ watch.needed }}</p>
    {% if watch.ok %}
      <p class="ok">Limit is sufficient.</p>
    {% else %}
      <p class="warn">Limit too low. Raise it on the host, then re-check on
        <a href="/settings">Settings</a>:</p>
      <pre>echo {{ watch.recommended }} &gt; /proc/sys/fs/inotify/max_user_watches</pre>
      <pre>sysctl -w fs.inotify.max_user_watches={{ watch.recommended }}</pre>
    {% endif %}
  {% endif %}
</section>
{% endblock %}
```

Create `mediascanmonitor/web/templates/servers.html`:

```html
{% extends "base.html" %}
{% block title %}Servers — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<h1>Servers</h1>

<div id="server-list">
  {% include "_servers_list.html" %}
</div>

<section class="card">
  <h2>Add a server</h2>
  <div id="form-error"></div>
  <form id="add-server" hx-post="/ui/servers" hx-target="#server-list" hx-swap="innerHTML">
    <label>Name <input type="text" name="name" required></label>
    <label>Type
      <select name="type" id="type-select">
        {% for t in server_types %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
      </select>
    </label>
    <label class="field-base_url">Base URL <input type="text" name="base_url"></label>
    <label class="field-secret">Token <input type="password" name="secret" autocomplete="new-password"></label>
    <label>Scan mode
      <select name="scan_mode" id="scan-mode-select">
        {% for m in scan_modes %}<option value="{{ m }}">{{ m }}</option>{% endfor %}
      </select>
    </label>
    <label>Debounce mode
      <select name="debounce_mode">
        {% for m in debounce_modes %}<option value="{{ m }}">{{ m }}</option>{% endfor %}
      </select>
    </label>
    <label>Debounce window (s) <input type="number" name="debounce_window_seconds" value="30"></label>
    <label>Retry attempts <input type="number" name="retry_attempts" value="3"></label>
    <label>Timeout (s) <input type="number" step="0.1" name="timeout_seconds" value="10"></label>
    <label><input type="checkbox" name="verify_tls" checked> Verify TLS</label>
    <label><input type="checkbox" name="enabled" checked> Enabled</label>
    <fieldset class="field-webhook">
      <legend>Webhook</legend>
      <label>Method <input type="text" name="webhook_method" placeholder="POST"></label>
      <label>Headers (JSON) <input type="text" name="webhook_headers_json"></label>
      <label>Body template <textarea name="webhook_body_template" rows="3"></textarea></label>
    </fieldset>
    <button type="submit">Add server</button>
  </form>
</section>

<script type="application/json" id="type-specs">{{ type_specs | tojson }}</script>
<script type="application/json" id="scan-modes-by-type">{{ scan_modes_by_type | tojson }}</script>
<script>
  // Data-driven per-type form behavior (invariant 6): no literal type names here — the maps come
  // from SERVER_TYPE_SPECS + supported_scan_modes, serialized server-side.
  (function () {
    const specs = JSON.parse(document.getElementById("type-specs").textContent);
    const modesByType = JSON.parse(document.getElementById("scan-modes-by-type").textContent);
    const typeSel = document.getElementById("type-select");
    const modeSel = document.getElementById("scan-mode-select");
    function apply() {
      const spec = specs[typeSel.value] || {};
      const form = document.getElementById("add-server");
      form.querySelector(".field-secret").style.display = spec.requires_secret ? "" : "none";
      form.querySelector(".field-base_url").style.display = spec.requires_base_url ? "" : "none";
      form.querySelector(".field-webhook").style.display = spec.is_webhook ? "" : "none";
      const modes = modesByType[typeSel.value] || [];
      modeSel.innerHTML = "";
      for (const m of modes) {
        const opt = document.createElement("option");
        opt.value = m; opt.textContent = m; modeSel.appendChild(opt);
      }
    }
    typeSel.addEventListener("change", apply);
    apply();
  })();
</script>
{% endblock %}
```

Create `mediascanmonitor/web/templates/server_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ server.name }} — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<p><a href="/servers">&larr; Servers</a></p>
<h1>{{ server.name }} <span class="badge">{{ server.type }}</span></h1>

<section class="card">
  <h2>Connection test</h2>
  <button hx-post="/api/servers/{{ server.id }}/test" hx-target="#test-result" hx-swap="innerHTML">
    Test
  </button>
  <pre id="test-result"></pre>
</section>

<section class="card">
  <h2>Edit</h2>
  <div id="edit-error"></div>
  <form hx-post="/ui/servers/{{ server.id }}/update" hx-target="#edit-result" hx-swap="innerHTML">
    <label>Name <input type="text" name="name" value="{{ server.name }}"></label>
    <label>Base URL <input type="text" name="base_url" value="{{ server.base_url }}"></label>
    <label>Token
      <input type="password" name="secret" autocomplete="new-password"
             placeholder="{% if server.has_secret %}(set — leave blank to keep){% else %}(none){% endif %}">
    </label>
    <label><input type="checkbox" name="clear_secret"> Clear stored token</label>
    <label>Scan mode
      <select name="scan_mode">
        {% for m in server.supported_scan_modes %}
          <option value="{{ m.value }}" {% if m == server.scan_mode %}selected{% endif %}>{{ m.value }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Debounce mode
      <select name="debounce_mode">
        {% for m in debounce_modes %}
          <option value="{{ m }}" {% if m == server.debounce_mode.value %}selected{% endif %}>{{ m }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Debounce window (s)
      <input type="number" name="debounce_window_seconds" value="{{ server.debounce_window_seconds }}"></label>
    <label>Retry attempts <input type="number" name="retry_attempts" value="{{ server.retry_attempts }}"></label>
    <label>Timeout (s)
      <input type="number" step="0.1" name="timeout_seconds" value="{{ server.timeout_seconds }}"></label>
    <label><input type="checkbox" name="verify_tls" {% if server.verify_tls %}checked{% endif %}> Verify TLS</label>
    <label><input type="checkbox" name="enabled" {% if server.enabled %}checked{% endif %}> Enabled</label>
    <button type="submit">Save</button>
  </form>
  <div id="edit-result"></div>
  <form hx-post="/ui/servers/{{ server.id }}/delete" hx-target="body" hx-swap="innerHTML"
        hx-confirm="Delete this server and all its folders?">
    <button type="submit" class="danger">Delete server</button>
  </form>
</section>

<section class="card">
  <h2>Folders</h2>
  <div id="folders">
    {% include "_folders.html" %}
  </div>
  <h3>Add a folder</h3>
  <div id="folder-error"></div>
  <form hx-post="/ui/servers/{{ server.id }}/folders" hx-target="#folders" hx-swap="innerHTML">
    <label>Path <input type="text" name="path" placeholder="/data/tv" required></label>
    <label>Library id <input type="text" name="library_id"></label>
    <label>Extensions <input type="text" name="extensions" placeholder="mkv, mp4"></label>
    <label><input type="checkbox" name="enabled" checked> Enabled</label>
    <button type="submit">Add folder</button>
  </form>
</section>
{% endblock %}
```

Create `mediascanmonitor/web/templates/settings.html`:

```html
{% extends "base.html" %}
{% block title %}Settings — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<h1>Settings</h1>

<section class="card">
  <h2>inotify gate</h2>
  <p>When <code>enforce</code>, the engine blocks until the kernel watch limit is sufficient.
     Set <code>off</code> to watch regardless (useful for small setups).</p>
  <form hx-post="/ui/settings" hx-target="#status-panel" hx-swap="innerHTML">
    <label>Gate
      <select name="inotify_gate">
        <option value="enforce" {% if inotify_gate == "enforce" %}selected{% endif %}>enforce</option>
        <option value="off" {% if inotify_gate == "off" %}selected{% endif %}>off</option>
      </select>
    </label>
    <button type="submit">Save</button>
  </form>
  <form hx-post="/ui/recheck" hx-target="#status-panel" hx-swap="innerHTML">
    <button type="submit">Re-check watch limit</button>
  </form>
</section>

<section class="card">
  <h2>Status</h2>
  <div id="status-panel">
    {% include "_status.html" %}
  </div>
</section>
{% endblock %}
```

Create `mediascanmonitor/web/templates/events.html`:

```html
{% extends "base.html" %}
{% block title %}Events — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<h1>Live events</h1>
<p>Streaming from <code>/events/stream</code> (most recent at top).</p>
<ul id="event-feed"></ul>
<script>
  // 6-line EventSource feed (no htmx SSE extension vendored — keeps the static surface to one file).
  (function () {
    const feed = document.getElementById("event-feed");
    const source = new EventSource("/events/stream");
    source.onmessage = function (e) {
      const r = JSON.parse(e.data);
      const li = document.createElement("li");
      const ok = r.ok ? "ok" : "fail";
      li.className = "event event-" + ok;
      li.textContent = r.ts + "  [" + r.server_name + "] " + r.event_type + " " + r.file_path +
        "  -> " + (r.ok ? "ok" : "FAIL") + (r.detail ? " (" + r.detail + ")" : "");
      feed.insertBefore(li, feed.firstChild);
    };
  })();
</script>
{% endblock %}
```

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/web/test_pages.py -v`
Expected: PASS — 8 passed. (`_servers_list.html`, `_folders.html`, `_status.html` are created in
Tasks 3–4; until then the `{% include %}`s will error — so create the three included partials now as
minimal stubs to keep this task green, OR sequence Task 3/4 first. To keep each task independently green,
create the three partials as their final versions now from Tasks 3/4's bodies below.)

> **Sequencing note for the implementer:** the page templates `{% include %}` three partials owned by
> Tasks 3–4 (`_servers_list.html`, `_folders.html`, `_status.html`). Create those three files **in this
> task** using the exact bodies given in Tasks 3 and 4, so `test_pages.py` renders. Their handlers land in
> Tasks 3–4; the partial *markup* is shared and can exist now.

Bodies to create now (final versions, reused by Tasks 3–4):

`mediascanmonitor/web/templates/_servers_list.html`:

```html
<table class="server-table">
  <thead><tr><th>Name</th><th>Type</th><th>Enabled</th><th></th></tr></thead>
  <tbody>
    {% for s in servers %}
    <tr>
      <td><a href="/servers/{{ s.id }}">{{ s.name }}</a></td>
      <td>{{ s.type.value if s.type is not string else s.type }}</td>
      <td>{{ "yes" if s.enabled else "no" }}</td>
      <td>
        <form hx-post="/ui/servers/{{ s.id }}/delete" hx-target="#server-list" hx-swap="innerHTML"
              hx-confirm="Delete {{ s.name }}?">
          <button type="submit" class="danger">Delete</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="4">No servers yet.</td></tr>
    {% endfor %}
  </tbody>
</table>
```

> `_servers_list.html` is rendered with `servers` being raw `Server` rows (from `repo.list_servers`) on
> the page route and after create/delete. `Server.type` is a `ServerType` (StrEnum) — render `s.type`
> directly (its `str()` is the bare value); the `is string` guard above is defensive and harmless.
> Simplify to `{{ s.type }}` if preferred.

`mediascanmonitor/web/templates/_folders.html`:

```html
<ul class="folder-list">
  {% for f in folders %}
  <li>
    <form hx-post="/ui/folders/{{ f.id }}/update" hx-target="#folders" hx-swap="innerHTML">
      <input type="text" name="path" value="{{ f.path }}">
      <input type="text" name="library_id" value="{{ f.library_id or '' }}">
      <input type="text" name="extensions" value="{{ f.extensions | join(', ') }}">
      <label><input type="checkbox" name="enabled" {% if f.enabled %}checked{% endif %}> on</label>
      <button type="submit">Save</button>
    </form>
    <form hx-post="/ui/folders/{{ f.id }}/delete" hx-target="#folders" hx-swap="innerHTML"
          hx-confirm="Delete folder {{ f.path }}?">
      <button type="submit" class="danger">Delete</button>
    </form>
  </li>
  {% else %}
  <li>No folders yet.</li>
  {% endfor %}
</ul>
```

> `_folders.html` renders `folders` as a list of `FolderRead` (has `.extensions: list[str]`,
> `.library_id`, `.enabled`, `.path`, `.id`).

`mediascanmonitor/web/templates/_status.html`:

```html
<p>Engine state: <span class="state state-{{ engine_state }}">{{ engine_state }}</span></p>
<p>inotify gate: <strong>{{ inotify_gate }}</strong></p>
<p>Servers: {{ enabled_server_count }} enabled / {{ server_count }} total</p>
{% if watch is none %}
  <p>No watches configured.</p>
{% elif watch.ok %}
  <p class="ok">Watch limit sufficient ({{ watch.current }} ≥ {{ watch.needed }}).</p>
{% else %}
  <p class="warn">Watch limit too low: {{ watch.current }} &lt; {{ watch.needed }}.
     Recommended: {{ watch.recommended }}.</p>
{% endif %}
```

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/pages.py tests/web/test_pages.py && mypy mediascanmonitor/web/pages.py`
Expected: clean. (If `ruff` reports an unused import at this stage, the offending name belongs to Task 3/4;
remove it and re-add there. The block above is sized for Task 2's usage.)

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/pages.py tests/web/test_pages.py mediascanmonitor/web/templates/
git commit -m "feat(web): dashboard, servers, detail, settings & events pages + nav/partials"
```

---

### Task 3: `/ui` server & folder form handlers (shared write-cores)

**Files:**
- Create: `tests/web/test_ui_forms.py`
- Modify: `mediascanmonitor/web/pages.py`
- Create: `mediascanmonitor/web/templates/_error.html`

**Interfaces:**
- Consumes: `apply_server_create`/`apply_server_update`/`apply_server_delete`/`apply_folder_create`/
  `apply_folder_update`/`apply_folder_delete` (`web/writes.py`, each `async`, raises `HTTPException(422)`
  on the §D token-required violation + calls `rebuild_engine`); `ServerCreate`/`ServerUpdate`/
  `FolderCreate`/`FolderUpdate` (`db/schemas.py`); `Form` (FastAPI); `Repo.list_servers`/`.list_folders`
  (off-thread, to re-render the swapped partial).
- Produces: `POST /ui/servers`, `POST /ui/servers/{id}/update`, `POST /ui/servers/{id}/delete`,
  `POST /ui/servers/{id}/folders`, `POST /ui/folders/{id}/update`, `POST /ui/folders/{id}/delete`.

**Error-handling decision (state it):** the write-cores raise `HTTPException(422)` on the §D token check.
htmx does **not** swap non-2xx responses by default, so each `/ui` handler **catches** that `HTTPException`
and returns the `_error.html` partial with **status 200** plus an `HX-Retarget`/`HX-Reswap` header pair so
htmx redirects the swap into the form's error slot (`#form-error` / `#edit-error` / `#folder-error`).
The JSON `/api/*` surface (02) keeps the real `422` for programmatic callers — only the HTML twin softens
the status so the browser always shows the message inline.

- [ ] **Step 1: Write the failing form tests**

Create `tests/web/test_ui_forms.py`:

```python
"""/ui form handlers: success swaps a partial, 422 renders inline, the write-core rebuilds."""

import httpx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate


def _seed_plex(repo) -> int:  # type: ignore[no-untyped-def]
    s = repo.create_server(
        ServerCreate(name="Plex", type=ServerType.plex, base_url="http://plex:32400", secret="tok")
    )
    return int(s.id)


def test_ui_create_webhook_server_swaps_list_and_rebuilds(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    before = engine.rebuild_calls
    resp = auth_client.post(
        "/ui/servers",
        data={
            "name": "Hook", "type": "webhook", "base_url": "https://hook.example",
            "scan_mode": "library", "debounce_mode": "off",
            "debounce_window_seconds": "30", "retry_attempts": "1", "timeout_seconds": "10",
        },
    )
    assert resp.status_code == 200
    assert "Hook" in resp.text  # the new server row is in the swapped list
    assert engine.rebuild_calls == before + 1
    assert any(s.name == "Hook" for s in repo.list_servers())


def test_ui_create_plex_without_token_renders_inline_error_no_rebuild(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    before = engine.rebuild_calls
    resp = auth_client.post(
        "/ui/servers",
        data={
            "name": "BadPlex", "type": "plex", "base_url": "http://plex:32400",
            "scan_mode": "targeted", "debounce_mode": "trailing",
            "debounce_window_seconds": "30", "retry_attempts": "3", "timeout_seconds": "10",
        },
    )
    assert resp.status_code == 200  # softened so htmx swaps the message
    assert "token" in resp.text.lower()
    assert resp.headers.get("hx-retarget") == "#form-error"
    assert engine.rebuild_calls == before  # core raised before writing/rebuilding
    assert not any(s.name == "BadPlex" for s in repo.list_servers())


def test_ui_update_server_keeps_secret_when_blank_and_rebuilds(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    before = engine.rebuild_calls
    resp = auth_client.post(
        f"/ui/servers/{sid}/update",
        data={
            "name": "Plex", "base_url": "http://plex:32400", "secret": "",
            "scan_mode": "targeted", "debounce_mode": "trailing",
            "debounce_window_seconds": "30", "retry_attempts": "3", "timeout_seconds": "10",
            "verify_tls": "on", "enabled": "on",
        },
    )
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
    assert repo.get_server(sid).secret_encrypted is not None  # blank secret left the token intact


def test_ui_delete_server_swaps_list_and_rebuilds(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    before = engine.rebuild_calls
    resp = auth_client.post(f"/ui/servers/{sid}/delete")
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
    assert repo.get_server(sid) is None


def test_ui_create_and_delete_folder(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    resp = auth_client.post(
        f"/ui/servers/{sid}/folders",
        data={"path": "/data/tv", "library_id": "2", "extensions": "mkv, mp4", "enabled": "on"},
    )
    assert resp.status_code == 200
    assert "/data/tv" in resp.text
    folders = repo.list_folders(sid)
    assert len(folders) == 1
    fid = folders[0].id

    resp2 = auth_client.post(f"/ui/folders/{fid}/delete")
    assert resp2.status_code == 200
    assert repo.list_folders(sid) == []


def test_ui_update_folder_replaces_extensions(
    auth_client: httpx.Client, repo  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    repo.create_folder(sid, FolderCreate(path="/data/tv", library_id="2", extensions=["mkv"]))
    fid = repo.list_folders(sid)[0].id
    resp = auth_client.post(
        f"/ui/folders/{fid}/update",
        data={"path": "/data/tv", "library_id": "2", "extensions": "mp4", "enabled": "on"},
    )
    assert resp.status_code == 200
    exts = [ft.extension for ft in repo.list_folders(sid)[0].filetypes]
    assert exts == ["mp4"]
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/web/test_ui_forms.py -v`
Expected: FAIL — `/ui/...` routes 404 / 405.

- [ ] **Step 3: Create the error partial**

Create `mediascanmonitor/web/templates/_error.html`:

```html
<div class="error" role="alert">{{ message }}</div>
```

- [ ] **Step 4: Add the `/ui` handlers to `web/pages.py`**

Extend the import block in `web/pages.py` (these names are now used). The full block is:

```python
import asyncio
import dataclasses
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.servers import registry
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS, ServerRead
from mediascanmonitor.web.deps import (
    get_engine,
    get_events_bus,
    get_repo,
    get_templates,
    require_page_auth,
)
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_folder_delete,
    apply_folder_update,
    apply_server_create,
    apply_server_delete,
    apply_server_update,
)
```

Add these helpers + handlers (after the page routes):

```python
def _split_extensions(raw: str) -> list[str]:
    """Parse a comma/whitespace-separated extensions field into a list (validators normalize further)."""
    return [part for part in re.split(r"[,\s]+", raw.strip()) if part]


def _error_partial(
    request: Request, templates: Jinja2Templates, message: str, target: str
) -> Response:
    """Render the inline error partial, retargeted (via htmx headers) into the form's error slot.

    Returns status 200 (htmx only swaps 2xx); the JSON /api surface keeps the real 422.
    """
    response = templates.TemplateResponse(
        request=request, name="_error.html", context={"message": message}
    )
    response.headers["HX-Retarget"] = target
    response.headers["HX-Reswap"] = "innerHTML"
    return response


async def _servers_list_response(
    request: Request, repo: Repo, templates: Jinja2Templates
) -> Response:
    servers = await asyncio.to_thread(repo.list_servers)
    return templates.TemplateResponse(
        request=request, name="_servers_list.html", context={"servers": servers}
    )


async def _folders_response(
    request: Request, repo: Repo, server_id: int, templates: Jinja2Templates
) -> Response:
    folders = [FolderRead.from_model(f) for f in await asyncio.to_thread(repo.list_folders, server_id)]
    return templates.TemplateResponse(
        request=request, name="_folders.html", context={"folders": folders}
    )


@router.post("/ui/servers")
async def ui_create_server(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    base_url: str = Form(""),
    secret: str = Form(""),
    scan_mode: str = Form(...),
    debounce_mode: str = Form(...),
    debounce_window_seconds: int = Form(30),
    retry_attempts: int = Form(3),
    timeout_seconds: float = Form(10.0),
    verify_tls: bool = Form(False),
    enabled: bool = Form(False),
    webhook_method: str = Form(""),
    webhook_headers_json: str = Form(""),
    webhook_body_template: str = Form(""),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Build the schema INSIDE the try: an invalid enum string (ServerType/ScanMode/DebounceMode)
    # or a pydantic field-validator failure raises ValueError, which must render the inline error
    # partial — not bubble to a 500. (Pydantic's ValidationError is a ValueError subclass.)
    try:
        data = ServerCreate(
            name=name,
            type=ServerType(type),
            base_url=base_url,
            secret=secret or None,
            scan_mode=ScanMode(scan_mode),
            debounce_mode=DebounceMode(debounce_mode),
            debounce_window_seconds=debounce_window_seconds,
            retry_attempts=retry_attempts,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
            enabled=enabled,
            webhook_method=webhook_method or None,
            webhook_headers_json=webhook_headers_json or None,
            webhook_body_template=webhook_body_template or None,
        )
        await apply_server_create(repo, engine, data)
    except HTTPException as exc:
        return _error_partial(request, templates, str(exc.detail), "#form-error")
    except ValueError as exc:
        return _error_partial(request, templates, str(exc), "#form-error")
    return await _servers_list_response(request, repo, templates)


@router.post("/ui/servers/{server_id}/update")
async def ui_update_server(
    request: Request,
    server_id: int,
    name: str = Form(...),
    base_url: str = Form(""),
    secret: str = Form(""),
    clear_secret: bool = Form(False),
    scan_mode: str = Form(...),
    debounce_mode: str = Form(...),
    debounce_window_seconds: int = Form(30),
    retry_attempts: int = Form(3),
    timeout_seconds: float = Form(10.0),
    verify_tls: bool = Form(False),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Build the schema INSIDE the try (enum/validator failures → ValueError → inline error, not 500).
    # apply_server_update raises KeyError if the server was deleted concurrently — render the error
    # partial rather than 500 (mirrors the /api twin translating KeyError → 404).
    try:
        # Secret tri-state via exclude_unset: omit when blank (keep), set None when "clear" ticked.
        fields: dict[str, Any] = {
            "name": name,
            "base_url": base_url,
            "scan_mode": ScanMode(scan_mode),
            "debounce_mode": DebounceMode(debounce_mode),
            "debounce_window_seconds": debounce_window_seconds,
            "retry_attempts": retry_attempts,
            "timeout_seconds": timeout_seconds,
            "verify_tls": verify_tls,
            "enabled": enabled,
        }
        if clear_secret:
            fields["secret"] = None
        elif secret:
            fields["secret"] = secret
        data = ServerUpdate(**fields)
        await apply_server_update(repo, engine, server_id, data)
    except HTTPException as exc:
        return _error_partial(request, templates, str(exc.detail), "#edit-error")
    except (ValueError, KeyError) as exc:
        return _error_partial(request, templates, str(exc), "#edit-error")
    return templates.TemplateResponse(
        request=request, name="_error.html", context={"message": "Saved."}
    )


@router.post("/ui/servers/{server_id}/delete")
async def ui_delete_server(
    request: Request,
    server_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    await apply_server_delete(repo, engine, server_id)
    return await _servers_list_response(request, repo, templates)


@router.post("/ui/servers/{server_id}/folders")
async def ui_create_folder(
    request: Request,
    server_id: int,
    path: str = Form(...),
    library_id: str = Form(""),
    extensions: str = Form(""),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    try:
        data = FolderCreate(
            path=path,
            library_id=library_id or None,
            extensions=_split_extensions(extensions),
            enabled=enabled,
        )
        await apply_folder_create(repo, engine, server_id, data)
    except (HTTPException, ValueError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#folder-error")
    return await _folders_response(request, repo, server_id, templates)


@router.post("/ui/folders/{folder_id}/update")
async def ui_update_folder(
    request: Request,
    folder_id: int,
    path: str = Form(...),
    library_id: str = Form(""),
    extensions: str = Form(""),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=f"folder {folder_id} not found")
    server_id = folder.server_id
    try:
        data = FolderUpdate(
            path=path,
            library_id=library_id or None,
            extensions=_split_extensions(extensions),
            enabled=enabled,
        )
        await apply_folder_update(repo, engine, folder_id, data)
    except (HTTPException, ValueError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#folder-error")
    return await _folders_response(request, repo, server_id, templates)


@router.post("/ui/folders/{folder_id}/delete")
async def ui_delete_folder(
    request: Request,
    folder_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=f"folder {folder_id} not found")
    server_id = folder.server_id
    await apply_folder_delete(repo, engine, folder_id)
    return await _folders_response(request, repo, server_id, templates)
```

Also add `FolderRead` to the `web/api_schemas` import (used by `_folders_response`):

```python
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS, FolderRead, ServerRead
```

> **`repo.get_folder`** is the §E read-one method added by 02 (`get_folder(folder_id) -> Folder | None`,
> filetypes force-loaded). It is consumed here to resolve a folder's `server_id` so the swap re-renders
> the right server's folder list. If 02 named it differently, match that name.

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/web/test_ui_forms.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 6: Lint + type-check**

Run: `ruff check mediascanmonitor/web/pages.py tests/web/test_ui_forms.py && mypy mediascanmonitor/web/pages.py`
Expected: clean.

> `type` as a parameter name shadows the builtin — ruff `E`/`B` do not flag a `Form` parameter named
> `type`, and FastAPI needs the form field literally named `type` to match the `<select name="type">`.
> Do not rename it to `type_`; that would break the form binding. (No `# noqa` needed.)

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/pages.py tests/web/test_ui_forms.py \
        mediascanmonitor/web/templates/_error.html
git commit -m "feat(web): /ui server+folder form handlers via shared write-cores with inline errors"
```

---

### Task 4: `/ui/settings` gate toggle + `/ui/recheck` (HTML twins of §H)

**Files:**
- Create: `tests/web/test_ui_settings.py`
- Modify: `mediascanmonitor/web/pages.py`

**Interfaces:**
- Consumes: `Repo.set_setting` (off-thread); `rebuild_engine` (`web/rebuild.py`, 02) — the same
  `set_setting` + `rebuild_engine` path as 03's `PUT /api/settings/inotify-gate` and
  `POST /api/engine/recheck` (contract §H). `_status_context` (Task 2).
- Produces: `POST /ui/settings`, `POST /ui/recheck` — each returns the `_status.html` partial.

**Decision (state it):** rather than HTTP-calling the JSON API from inside a handler, `/ui/settings` and
`/ui/recheck` are **thin twins** that share the same logical core (`set_setting` + `rebuild_engine` for the
toggle; `rebuild_engine` alone for recheck) and return an HTML partial. They validate `inotify_gate`
against the 2-member set `{"enforce", "off"}` (mirroring §H's literal). This keeps the value domain
validated without depending on an unfrozen 03 helper name.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_ui_settings.py`:

```python
"""/ui settings: gate toggle persists + rebuilds; recheck rebuilds. HTML status partial out."""

import httpx

from mediascanmonitor.engine import EngineState


def test_ui_settings_persists_gate_and_rebuilds(
    auth_client: httpx.Client, repo, engine  # type: ignore[no-untyped-def]
) -> None:
    engine.state = EngineState.running
    before = engine.rebuild_calls
    resp = auth_client.post("/ui/settings", data={"inotify_gate": "off"})
    assert resp.status_code == 200
    assert "off" in resp.text  # status partial reflects the new gate
    assert repo.get_setting("inotify_gate") == "off"
    assert engine.rebuild_calls == before + 1


def test_ui_settings_rejects_bad_value(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    resp = auth_client.post("/ui/settings", data={"inotify_gate": "banana"})
    assert resp.status_code == 422  # validated against the 2-member literal


def test_ui_recheck_rebuilds(
    auth_client: httpx.Client, engine  # type: ignore[no-untyped-def]
) -> None:
    engine.state = EngineState.blocked
    before = engine.rebuild_calls
    resp = auth_client.post("/ui/recheck")
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/web/test_ui_settings.py -v`
Expected: FAIL — `/ui/settings` and `/ui/recheck` 404.

- [ ] **Step 3: Add the handlers to `web/pages.py`**

Add `rebuild_engine` to the writes import:

```python
from mediascanmonitor.web.rebuild import rebuild_engine
```

(group it with the other `mediascanmonitor.web.*` imports). Then add the handlers:

```python
_INOTIFY_GATE_VALUES = frozenset({"enforce", "off"})


@router.post("/ui/settings")
async def ui_settings(
    request: Request,
    inotify_gate: str = Form(...),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if inotify_gate not in _INOTIFY_GATE_VALUES:
        raise HTTPException(status_code=422, detail="inotify_gate must be 'enforce' or 'off'")
    await asyncio.to_thread(repo.set_setting, "inotify_gate", inotify_gate)
    await rebuild_engine(engine)  # flipping to off can recover a blocked engine (§H/§I)
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="_status.html", context=context)


@router.post("/ui/recheck")
async def ui_recheck(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    await rebuild_engine(engine)  # re-evaluate the gate after an out-of-band host limit change (§H)
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="_status.html", context=context)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/web/test_ui_settings.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/web/pages.py tests/web/test_ui_settings.py && mypy mediascanmonitor/web/pages.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/pages.py tests/web/test_ui_settings.py
git commit -m "feat(web): /ui/settings gate toggle + /ui/recheck HTML twins (set_setting+rebuild)"
```

---

### Task 5: Static assets (vendored htmx + app.css) + mount + smoke test

**Files:**
- Create: `tests/web/test_static.py`
- Modify: `mediascanmonitor/web/app.py`
- Create: `mediascanmonitor/web/static/app.css`, `mediascanmonitor/web/static/htmx.min.js`

**Interfaces:**
- Consumes: `StaticFiles` (`fastapi.staticfiles`).
- Produces: `/static/*` mount on the app; `app.css`; vendored `htmx.min.js`.

**htmx delivery decision (vendored, not CDN):** the container is self-hosted and may run without outbound
internet (media bind-mounts are local); a CDN `<script>` would break the UI offline and add an external
trust dependency / CSP hole. So htmx is **vendored** as a pinned static file under `web/static/`, served
by our own `StaticFiles` mount. **Do not** copy a version number from memory — see Step 1.

- [ ] **Step 1: Vendor htmx at the current pinned version (CLAUDE rule 1)**

Determine the current stable htmx release from the official source (https://htmx.org / the
`bigskysoftware/htmx` GitHub releases) — **do not trust a version from memory or from this doc**. Then
download that exact minified build and record the version + SRI hash. Example shape (substitute the real
current version `X.Y.Z` and the real published file):

```bash
HTMX_VERSION="X.Y.Z"   # <- the current stable, verified at download time
curl -fsSL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" \
  -o mediascanmonitor/web/static/htmx.min.js
# record the integrity hash for the changelog / a future CSP:
openssl dgst -sha384 -binary mediascanmonitor/web/static/htmx.min.js | openssl base64 -A
```

Note the resolved version + `sha384-...` hash in the commit message. (base.html, from 01, already
references `/static/htmx.min.js`; no `<script integrity=...>` is required for a same-origin self-hosted
file, but record the hash so the pin is auditable.)

- [ ] **Step 2: Write the failing smoke test**

Create `tests/web/test_static.py`:

```python
"""Static assets are served from /static."""

import httpx


def test_app_css_served(client: httpx.Client) -> None:
    resp = client.get("/static/app.css")  # static is on the allow-list — no auth needed
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_htmx_served(client: httpx.Client) -> None:
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert resp.content  # non-empty vendored bundle
```

- [ ] **Step 3: Run — expect FAIL**

Run: `pytest tests/web/test_static.py -v`
Expected: FAIL — `/static/app.css` 404 (no mount yet; css file absent).

- [ ] **Step 4: Write `app.css`**

Create `mediascanmonitor/web/static/app.css`:

```css
:root { --fg: #1c2128; --muted: #57606a; --ok: #1a7f37; --warn: #9a6700; --err: #cf222e; --line: #d0d7de; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: #f6f8fa; }
.nav { display: flex; gap: 1rem; align-items: center; padding: .75rem 1.25rem; background: #fff;
       border-bottom: 1px solid var(--line); }
.nav a { text-decoration: none; color: var(--fg); font-weight: 600; }
.nav a:hover { color: #0969da; }
.nav-logout { margin-left: auto; }
h1, h2, section { margin: 1rem 1.25rem; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.25rem; }
label { display: block; margin: .4rem 0; }
input, select, textarea { font: inherit; padding: .3rem .4rem; }
button { font: inherit; padding: .35rem .8rem; border: 1px solid var(--line); border-radius: 6px;
         background: #f3f4f6; cursor: pointer; }
button:hover { background: #eaeef2; }
button.danger { color: #fff; background: var(--err); border-color: var(--err); }
.state { font-weight: 700; text-transform: uppercase; font-size: .8em; }
.state-running { color: var(--ok); }
.state-blocked { color: var(--warn); }
.state-stopped, .state-starting { color: var(--muted); }
.ok { color: var(--ok); } .warn { color: var(--warn); } .error { color: var(--err); font-weight: 600; }
pre { background: #f6f8fa; border: 1px solid var(--line); border-radius: 6px; padding: .5rem; overflow:auto; }
.badge { font-size: .7em; background: #ddf4ff; color: #0969da; padding: .1rem .4rem; border-radius: 999px; }
.server-table { border-collapse: collapse; width: calc(100% - 2.5rem); }
.server-table th, .server-table td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line); }
.folder-list { list-style: none; padding: 0; }
.folder-list li { padding: .5rem 0; border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: .4rem; }
#event-feed { list-style: none; padding: 0 1.25rem; font-family: ui-monospace, monospace; font-size: .85em; }
.event { padding: .2rem 0; border-bottom: 1px solid var(--line); }
.event-fail { color: var(--err); }
```

- [ ] **Step 5: Mount `/static` in `create_app`**

Modify `mediascanmonitor/web/app.py`. Add imports:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

and inside `create_app`, after the routers are included, mount the static dir (keep all existing lines):

```python
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
```

- [ ] **Step 6: Run — expect PASS**

Run: `pytest tests/web/test_static.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 7: Lint + type-check**

Run: `ruff check mediascanmonitor/web/app.py tests/web/test_static.py && mypy mediascanmonitor/web/app.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add mediascanmonitor/web/app.py mediascanmonitor/web/static/ tests/web/test_static.py
git commit -m "feat(web): mount /static + vendored htmx (pinned) and app.css"
```

---

### Task 6: Full-suite verification gate + followups

**Files:**
- Modify: `docs/FOLLOWUPS.md` (record deferred UI polish)

- [ ] **Step 1: Record deferred UI enhancements**

Modify `docs/FOLLOWUPS.md` — under the Phase 4 / "later" section, add:

```markdown
- [ ] Server "Test" button renders raw JSON from POST /api/servers/{id}/test into a <pre>; a prettier
      HTML twin (/ui/servers/{id}/test) is deferred. → phase3-04 dashboard plan
- [ ] Dashboard/events live-refresh polish (poll /api/status, htmx SSE extension) — baseline ships
      server-rendered status + a plain EventSource feed. → phase3-04 dashboard plan
- [ ] library_id discovery dropdowns (needs ServerAdapter.list_libraries) — UI ships free-text. → phase3 README
```

- [ ] **Step 2: Run the full gate**

Run: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`
Expected: all green. (If `ruff format --check` flags `web/pages.py` after the multi-task paste, run
`ruff format mediascanmonitor/web/pages.py` then re-run the gate — formatting is advisory per CLAUDE.md;
re-normalize before asserting clean.)

- [ ] **Step 3: Commit**

```bash
git add docs/FOLLOWUPS.md
git commit -m "docs(followups): defer UI Test-button HTML twin + live-refresh polish"
```

---

## Self-Review

**Spec coverage (task spine):**

1. **SSE `GET /events/stream`** — Task 1: `StreamingResponse` over `_event_generator` (replay
   `bus.recent()` → `bus.subscribe()` frames, break on `is_disconnected()`), mounted in `create_app`;
   `test_events_stream_replays_recent_records` (async `aclient`) + `test_events_stream_requires_auth`
   (anon `client` 303). No `sse-starlette`; race noted. ✓
2. **Page routes** — Task 2: `/`, `/servers`, `/servers/{id}`, `/settings`, `/events`, each
   `require_page_auth`, rendered via `app.state.templates`; 200-authed / 303-anon / key-content tests;
   form fields driven by `SERVER_TYPE_SPECS` + `supported_scan_modes` (no type literals — invariant 6). ✓
3. **`/ui` form handlers** — Task 3: server+folder create/update/delete parse `Form(...)`, call the
   shared `apply_*` write-cores (off-thread write + §D 422 + `rebuild_engine`), return HTML partials;
   success-swap, inline-422-error (`HX-Retarget`), and rebuild-invoked assertions. ✓
4. **`/ui/settings` + `/ui/recheck`** — Task 4: gate toggle (`set_setting` + `rebuild_engine`, validated
   to `{enforce, off}`) and recheck (`rebuild_engine`), HTML status partial; persist + rebuild assertions. ✓
5. **Static + polish** — Task 5: vendored pinned `htmx.min.js` + `app.css` under `web/static`, mounted at
   `/static`; `/static/app.css` 200 smoke. ✓

**Invariants:** secrets never rendered (write-only secret input, `ServerRead.has_secret` only — inv 1);
all routes guarded except static (inv 2); repo I/O via `asyncio.to_thread` (inv 3); every `/ui` write goes
through the shared write-core so it rebuilds + enforces the 422 (inv 4 / §K); web serves regardless of
engine state (inv 5); no server-type literal branching — `_type_specs`/`_scan_modes_by_type` data-drive
the form (inv 6); only `engine.py`/`cli.py`/`db` touched by other sub-plans, this one adds `web/pages.py`
+ templates/static + two additive `create_app` edits (inv 7).

**Placeholder scan:** none — complete code + templates + commands throughout.

**Decisions recorded:** htmx **vendored** (offline/self-hosted, no CDN); `/ui` settings/recheck are HTML
**twins** sharing `set_setting`+`rebuild_engine` (not internal HTTP calls); `/ui` 422 softened to a 200 +
`HX-Retarget` error partial while `/api` keeps the real 422; Test button posts to the existing JSON
`/api/servers/{id}/test` (HTML twin deferred to FOLLOWUPS); live feed uses a 6-line `EventSource` (no SSE
extension vendored).

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-20-phase3-04-dashboard-sse.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session using `executing-plans`, batched with checkpoints.

Which approach?
