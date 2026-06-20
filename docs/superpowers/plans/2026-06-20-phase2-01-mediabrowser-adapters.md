# Phase 2 — Sub-plan 01: MediaBrowser Adapters (Emby + Jellyfin) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Emby and Jellyfin notification adapters — two MediaBrowser-derived siblings that
refresh a whole library item via `POST /Items/{library_id}/Refresh`, each self-registering into the
Phase 1 adapter registry.

**Architecture:** Two independent, self-contained modules under `mediascanmonitor/servers/`, each
subclassing the frozen `ServerAdapter` ABC (Phase 1 contract §7). They are built in one plan because
their API shape is near-identical, but they share **no base class** — backend quirks (Emby's
`X-Emby-Token` header vs. Jellyfin's `Authorization: MediaBrowser Token="…"` plus its mandatory
`metadataRefreshMode`/`imageRefreshMode` query params) live in each adapter, so a future divergence
of either API touches only its own file (CLAUDE.md rule 2). Both deliberately do **library-refresh
only** — Emby/Jellyfin *do* support path-targeted notifications (`POST /Library/Media/Updated`),
but per-folder targeting for them is deferred (see the Phase 2 README, convention 2).

**Tech Stack:** Python 3.14, `httpx==0.28.1` (async), `tenacity==9.1.4` (retry, via `servers/http.py`),
`respx==0.23.1` (test transport mock), `pytest==9.1.0` + `pytest-asyncio==1.4.0`. `ruff`/`mypy --strict`
clean, line length 100. PEP 649 annotations — **no** `from __future__ import annotations`.

## Global Constraints

- **Frozen contract (Phase 1 §7), consumed not changed:** subclass `ServerAdapter`; set `server_type`
  + `supported_scan_modes` ClassVars; implement `async trigger(req) -> TriggerResult` and
  `async test() -> TestResult`. Do not edit `servers/{base,registry,http}.py` or any contract type.
- **Library-refresh only:** `supported_scan_modes = frozenset({ScanMode.library})`; `trigger()` uses
  `req.library_id`, ignores `req.scan_path`.
- **Secret in header only**, never in the URL/query, never logged (contract invariant 3).
- **Verify at implement-time (rule 1):** confirm the refresh path, the `test()` probe endpoint
  (`GET /System/Info`), and the exact auth-header format against current Emby/Jellyfin API docs
  before pinning. The values below are the documented defaults.
- **No new dependencies.**
- Verification gate per task’s lint/type step and the final gate:
  `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest` — green.

---

## Prerequisites (hard dependency — do not start until merged)

Phase 1 **sub-plan 03** must be merged. This plan consumes, unchanged:

- `mediascanmonitor/servers/base.py` — `ServerAdapter`, `TriggerResult`, `TestResult`.
- `mediascanmonitor/servers/registry.py` — `register`, `get_adapter_class`, `create_adapter`.
- `mediascanmonitor/servers/http.py` — `request_with_retry`.
- `mediascanmonitor/servers/__init__.py` — the self-registration import block.
- `tests/servers/conftest.py` — the builders `make_plex_runtime` (a **generic** `ServerRuntime`
  builder; the `type`/`base_url`/`scan_mode`/`secret`/`retry_attempts` keywords let it build any
  server type) and `make_scan_request`, plus the `client` fixture.

It also consumes the frozen types: `ScanMode`/`ServerType` (`db/models.py`), `ScanRequest`
(`pipeline/events.py`), `ServerRuntime` (`config/runtime.py`).

## File structure (what this plan builds)

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/servers/emby.py` | `EmbyAdapter` — `POST /Items/{library_id}/Refresh?Recursive=true`, `X-Emby-Token` header, `test()` → `GET /System/Info`. |
| `mediascanmonitor/servers/jellyfin.py` | `JellyfinAdapter` — `POST /Items/{library_id}/Refresh?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default`, `Authorization: MediaBrowser Token="…"`, `test()` → `GET /System/Info`. |
| `mediascanmonitor/servers/__init__.py` | **Modify:** append one self-registration import per adapter. |
| `tests/servers/test_emby.py` | Emby unit tests (metadata, registry, URL/method/header, classification, `test()`). |
| `tests/servers/test_jellyfin.py` | Jellyfin unit tests (same matrix + the `MediaBrowser Token=` format + extra query params). |

---

### Task 1: Emby adapter (`servers/emby.py`) + self-registration

**Files:**
- Create: `tests/servers/test_emby.py`
- Create: `mediascanmonitor/servers/emby.py`
- Modify: `mediascanmonitor/servers/__init__.py`

**Interfaces:**
- Consumes: `ServerAdapter`/`TriggerResult`/`TestResult` (`servers/base.py`); `register`,
  `get_adapter_class`, `create_adapter` (`servers/registry.py`); `request_with_retry`
  (`servers/http.py`); `ScanRequest` (`pipeline/events.py`); `ScanMode`, `ServerType`
  (`db/models.py`); `make_plex_runtime`, `make_scan_request`, `client` (`tests/servers/conftest.py`).
- Produces: `EmbyAdapter` (registered for `ServerType.emby`), used later by the engine/dispatcher
  via `create_adapter`.

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_emby.py`:

```python
"""EmbyAdapter: exact URL/method/header, success/failure classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.emby import EmbyAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://emby.example:8096"
REFRESH = f"{BASE}/Items/5/Refresh"
INFO = f"{BASE}/System/Info"


def emby_runtime(
    *, secret: str | None = "tok-secret", retry_attempts: int = 1
) -> ServerRuntime:
    return make_runtime(
        type=ServerType.emby,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library, scan_path=None, library_id="5", scan_key="lib:5"
    )


def test_emby_class_metadata() -> None:
    assert EmbyAdapter.server_type is ServerType.emby
    assert EmbyAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_emby_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.emby) is EmbyAdapter


async def test_create_adapter_builds_emby(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(emby_runtime(), client)
    assert isinstance(adapter, EmbyAdapter)
    assert adapter.client is client


@respx.mock
async def test_library_trigger_posts_recursive_with_token_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(REFRESH).mock(return_value=httpx.Response(200))
    adapter = EmbyAdapter(emby_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.params["Recursive"] == "true"
    # token in the HEADER, never in the URL
    assert request.headers["X-Emby-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(
    client: httpx.AsyncClient, status: int
) -> None:
    respx.post(REFRESH).mock(return_value=httpx.Response(status))
    adapter = EmbyAdapter(emby_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = EmbyAdapter(emby_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_system_info_with_token(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(INFO).mock(return_value=httpx.Response(200))
    adapter = EmbyAdapter(emby_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["X-Emby-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(INFO).mock(return_value=httpx.Response(401))
    adapter = EmbyAdapter(emby_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_emby.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.servers.emby'`.

- [ ] **Step 3: Implement `servers/emby.py`**

Create `mediascanmonitor/servers/emby.py`:

```python
"""Emby notification adapter (contract §7).

------------------------------------------------------------------------------
EMBY API QUIRKS (kept here so the watcher/pipeline never special-case Emby):

* Library refresh (no native path targeting — library mode only):
    POST {base_url}/Items/{library_id}/Refresh?Recursive=true
  Emby refreshes that library item and its descendants asynchronously. The
  configured ``library_id`` is the library/collection item id (set in the UI).

* Auth: the token goes in the ``X-Emby-Token`` HEADER. Never put it in the URL
  so it cannot leak into logs.

* Success: Emby answers 2xx (usually 204, empty body). We treat any 2xx as ok;
  there is no per-item completion signal to await.

* test(): GET {base_url}/System/Info with the token proves auth + reachability.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the Refresh path, the
System/Info probe, and the X-Emby-Token header against current Emby API docs.
------------------------------------------------------------------------------
"""

from typing import ClassVar

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


@register
class EmbyAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.emby
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})

    def _headers(self) -> dict[str, str]:
        # Token in header only — never in the URL (keeps it out of logs).
        return {"X-Emby-Token": self.server.secret or ""}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/Items/{req.library_id}/Refresh?Recursive=true"
        try:
            resp = await request_with_retry(
                self.client,
                "POST",
                url,
                attempts=self.server.retry_attempts,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(
                ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}"
            )
        if resp.is_success:
            return TriggerResult(
                ok=True, status_code=resp.status_code, detail="Emby refresh triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/System/Info"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TestResult(ok=True, detail="reachable")
        return TestResult(ok=False, detail=f"HTTP {resp.status_code}")
```

- [ ] **Step 4: Self-register the adapter on package import**

Modify `mediascanmonitor/servers/__init__.py` — append one line below the existing registration block
(the one that imports `plex`), so `EmbyAdapter` registers whenever the package is imported:

```python
from mediascanmonitor.servers import emby as _emby  # noqa: F401  (registration side effect)
```

- [ ] **Step 5: Run the Emby tests to verify they pass**

Run: `pytest tests/servers/test_emby.py -v`
Expected: PASS — 9 passed (the parametrized `status` test collects 2 items).

- [ ] **Step 6: Lint + type-check the new code**

Run: `ruff check mediascanmonitor/servers/emby.py mediascanmonitor/servers/__init__.py tests/servers/test_emby.py && mypy mediascanmonitor/servers/emby.py mediascanmonitor/servers/__init__.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add tests/servers/test_emby.py mediascanmonitor/servers/emby.py \
        mediascanmonitor/servers/__init__.py
git commit -m "feat(servers): add Emby library-refresh adapter with test()"
```

---

### Task 2: Jellyfin adapter (`servers/jellyfin.py`) + self-registration

**Files:**
- Create: `tests/servers/test_jellyfin.py`
- Create: `mediascanmonitor/servers/jellyfin.py`
- Modify: `mediascanmonitor/servers/__init__.py`

**Interfaces:**
- Consumes: same as Task 1.
- Produces: `JellyfinAdapter` (registered for `ServerType.jellyfin`).

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_jellyfin.py`:

```python
"""JellyfinAdapter: MediaBrowser auth format, mandatory refresh query params, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.jellyfin import JellyfinAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://jellyfin.example:8096"
REFRESH = f"{BASE}/Items/7/Refresh"
INFO = f"{BASE}/System/Info"


def jf_runtime(
    *, secret: str | None = "tok-secret", retry_attempts: int = 1
) -> ServerRuntime:
    return make_runtime(
        type=ServerType.jellyfin,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library, scan_path=None, library_id="7", scan_key="lib:7"
    )


def test_jellyfin_class_metadata() -> None:
    assert JellyfinAdapter.server_type is ServerType.jellyfin
    assert JellyfinAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_jellyfin_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.jellyfin) is JellyfinAdapter


@respx.mock
async def test_library_trigger_sends_refresh_modes_and_mediabrowser_auth(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(REFRESH).mock(return_value=httpx.Response(204))
    adapter = JellyfinAdapter(jf_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 204
    request = route.calls.last.request
    assert request.method == "POST"
    # Jellyfin requires all three query params on a recursive refresh
    assert request.url.params["Recursive"] == "true"
    assert request.url.params["metadataRefreshMode"] == "Default"
    assert request.url.params["imageRefreshMode"] == "Default"
    # exact MediaBrowser auth header format; token never in the URL
    assert request.headers["Authorization"] == 'MediaBrowser Token="tok-secret"'
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(
    client: httpx.AsyncClient, status: int
) -> None:
    respx.post(REFRESH).mock(return_value=httpx.Response(status))
    adapter = JellyfinAdapter(jf_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = JellyfinAdapter(jf_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_test_happy_path_hits_system_info_with_mediabrowser_auth(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(INFO).mock(return_value=httpx.Response(200))
    adapter = JellyfinAdapter(jf_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.headers["Authorization"] == 'MediaBrowser Token="tok-secret"'
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(INFO).mock(return_value=httpx.Response(401))
    adapter = JellyfinAdapter(jf_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_jellyfin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.servers.jellyfin'`.

- [ ] **Step 3: Implement `servers/jellyfin.py`**

Create `mediascanmonitor/servers/jellyfin.py`:

```python
"""Jellyfin notification adapter (contract §7).

------------------------------------------------------------------------------
JELLYFIN API QUIRKS (kept here so the watcher/pipeline never special-case it):

* Library refresh (no native path targeting — library mode only):
    POST {base_url}/Items/{library_id}/Refresh
        ?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default
  All three query params are required for a recursive refresh. The configured
  ``library_id`` is the collection-folder id (Phase 3's UI will help find it via
  GET /Library/VirtualFolders; the adapter takes it as given).

* Auth: Authorization: MediaBrowser Token="{token}" HEADER (note the literal
  ``MediaBrowser`` scheme and the DOUBLE-QUOTED token). Never in the URL.

* Success: Jellyfin answers 2xx (usually 204). We treat any 2xx as ok.

* test(): GET {base_url}/System/Info with the token proves auth + reachability.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the Refresh path + query
params, the System/Info probe, and the MediaBrowser auth header format against
current Jellyfin API docs.
------------------------------------------------------------------------------
"""

from typing import ClassVar

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


@register
class JellyfinAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.jellyfin
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})

    def _headers(self) -> dict[str, str]:
        # MediaBrowser scheme, double-quoted token; header only, never in the URL.
        return {"Authorization": f'MediaBrowser Token="{self.server.secret or ""}"'}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = (
            f"{base}/Items/{req.library_id}/Refresh"
            "?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default"
        )
        try:
            resp = await request_with_retry(
                self.client,
                "POST",
                url,
                attempts=self.server.retry_attempts,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(
                ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}"
            )
        if resp.is_success:
            return TriggerResult(
                ok=True, status_code=resp.status_code, detail="Jellyfin refresh triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/System/Info"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TestResult(ok=True, detail="reachable")
        return TestResult(ok=False, detail=f"HTTP {resp.status_code}")
```

- [ ] **Step 4: Self-register the adapter on package import**

Modify `mediascanmonitor/servers/__init__.py` — append one line below the `emby` import:

```python
from mediascanmonitor.servers import jellyfin as _jellyfin  # noqa: F401  (registration side effect)
```

- [ ] **Step 5: Run the Jellyfin tests to verify they pass**

Run: `pytest tests/servers/test_jellyfin.py -v`
Expected: PASS — 8 passed (the parametrized `status` test collects 2 items).

- [ ] **Step 6: Lint + type-check the new code**

Run: `ruff check mediascanmonitor/servers/jellyfin.py mediascanmonitor/servers/__init__.py tests/servers/test_jellyfin.py && mypy mediascanmonitor/servers/jellyfin.py mediascanmonitor/servers/__init__.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add tests/servers/test_jellyfin.py mediascanmonitor/servers/jellyfin.py \
        mediascanmonitor/servers/__init__.py
git commit -m "feat(servers): add Jellyfin library-refresh adapter with test()"
```

---

### Task 3: Full-suite verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full gate**

Run: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`
Expected: ruff "All checks passed!"; ruff format reports no files reformatted; mypy "Success";
pytest all green (the new `test_emby.py` + `test_jellyfin.py` included). Both adapters resolvable
via `registry.get_adapter_class(ServerType.emby)` / `...jellyfin`.

- [ ] **Step 2: (If anything is red) fix and re-run**

Address any failure, then re-run Step 1 until green. No commit needed if Tasks 1–2 already committed
and nothing changed here.

---

## Self-Review

**Spec coverage (every Phase 2 convention + this plan's goal):**

- Emby adapter: `POST /Items/{id}/Refresh?Recursive=true`, `X-Emby-Token` header — Task 1,
  `test_library_trigger_posts_recursive_with_token_header`. ✓
- Jellyfin adapter: `POST /Items/{id}/Refresh` with all three query params, `MediaBrowser Token="…"`
  header — Task 2, `test_library_trigger_sends_refresh_modes_and_mediabrowser_auth`. ✓
- Library-refresh only (`supported_scan_modes == {library}`) — Task 1/2 `*_class_metadata`. ✓
- Self-registration into the registry — Task 1/2 `*_is_registered` + `create_adapter` (Task 1). ✓
- Secret in header, never in URL — every trigger/test test asserts `secret not in str(url)`. ✓
- Success/HTTP-error/transport-error classification → `TriggerResult` — Task 1/2 trigger tests. ✓
- `test()` auth+reachability via `GET /System/Info` — Task 1/2 `test_test_*`. ✓
- No base class shared between the two adapters — each module is self-contained. ✓
- No new dependencies; frozen contract untouched — only new files + one-line `__init__` appends. ✓

**Placeholder scan:** none — every step has complete test + implementation code and exact commands.

**Type consistency:** `EmbyAdapter`/`JellyfinAdapter` set `server_type`/`supported_scan_modes`
ClassVars and implement `trigger(self, req: ScanRequest) -> TriggerResult` / `test(self) -> TestResult`
exactly as the frozen ABC declares; `request_with_retry(self.client, method, url, *, attempts, **kwargs)`
call matches the contract signature.

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-20-phase2-01-mediabrowser-adapters.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks
   (two-stage: spec compliance, then code quality), fast iteration.
2. **Inline Execution** — execute the tasks in this session using `executing-plans`, batched with
   checkpoints for review.

Which approach?
