# Phase 2 — Sub-plan 02: Audiobookshelf Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Audiobookshelf (ABS) notification adapter — a library-scan backend that posts to
`POST /api/libraries/{library_id}/scan` with `Bearer` auth, self-registering into the Phase 1 adapter
registry.

**Architecture:** One self-contained module under `mediascanmonitor/servers/`, subclassing the frozen
`ServerAdapter` ABC (Phase 1 contract §7). ABS has its own API shape (distinct from the MediaBrowser
`/Items/{id}/Refresh` siblings), so it is its own plan/file. **Library-scan only by deliberate
choice:** the `/api/libraries/{id}/scan` endpoint this adapter uses is whole-library, but ABS *does*
support path-targeted notifications via `POST /api/watcher/update` (ABS ≥2.9.0) — adopting that is a
deferred enhancement (see Phase 2 README convention 2 and `docs/FOLLOWUPS.md`).

**Tech Stack:** Python 3.14, `httpx==0.28.1`, `tenacity==9.1.4` (via `servers/http.py`),
`respx==0.23.1`, `pytest==9.1.0` + `pytest-asyncio==1.4.0`. `ruff`/`mypy --strict` clean, line length
100. PEP 649 annotations — **no** `from __future__ import annotations`.

## Global Constraints

- **Frozen contract (Phase 1 §7), consumed not changed:** subclass `ServerAdapter`; set `server_type`
  + `supported_scan_modes`; implement `async trigger`/`async test`. Do not edit
  `servers/{base,registry,http}.py` or any contract type.
- **Library-scan only:** `supported_scan_modes = frozenset({ScanMode.library})`; `trigger()` uses
  `req.library_id`, ignores `req.scan_path`.
- **Secret in header only** (`Authorization: Bearer {token}`), never in the URL, never logged
  (contract invariant 3).
- **Verify at implement-time (rule 1):** confirm the scan path `POST /api/libraries/{id}/scan`, the
  `test()` probe endpoint (`GET /api/me`), the `Bearer` scheme, and whether `?force=1` is needed
  against the current Audiobookshelf API docs before pinning.
- **No new dependencies.**
- Verification gate: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`.

---

## Prerequisites (hard dependency — do not start until merged)

Phase 1 **sub-plan 03** must be merged. This plan consumes, unchanged: `servers/base.py`
(`ServerAdapter`, `TriggerResult`, `TestResult`), `servers/registry.py` (`register`,
`get_adapter_class`, `create_adapter`), `servers/http.py` (`request_with_retry`),
`servers/__init__.py` (the self-registration block), and `tests/servers/conftest.py` (the generic
`make_plex_runtime` builder, `make_scan_request`, and the `client` fixture). It consumes the frozen
types `ScanMode`/`ServerType`, `ScanRequest`, `ServerRuntime`.

This plan is **independent of sub-plan 01**; it can be built before, after, or in parallel. The only
shared file is `servers/__init__.py` (one appended import line).

## File structure (what this plan builds)

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/servers/audiobookshelf.py` | `AudiobookshelfAdapter` — `POST /api/libraries/{library_id}/scan`, `Bearer` auth, `test()` → `GET /api/me`. |
| `mediascanmonitor/servers/__init__.py` | **Modify:** append one self-registration import line. |
| `tests/servers/test_audiobookshelf.py` | ABS unit tests (metadata, registry, URL/method/`Bearer` header, classification, `test()`). |

---

### Task 1: Audiobookshelf adapter (`servers/audiobookshelf.py`) + self-registration

**Files:**
- Create: `tests/servers/test_audiobookshelf.py`
- Create: `mediascanmonitor/servers/audiobookshelf.py`
- Modify: `mediascanmonitor/servers/__init__.py`

**Interfaces:**
- Consumes: `ServerAdapter`/`TriggerResult`/`TestResult` (`servers/base.py`); `register`,
  `get_adapter_class`, `create_adapter` (`servers/registry.py`); `request_with_retry`
  (`servers/http.py`); `ScanRequest` (`pipeline/events.py`); `ScanMode`, `ServerType`
  (`db/models.py`); `make_plex_runtime`, `make_scan_request`, `client` (`tests/servers/conftest.py`).
- Produces: `AudiobookshelfAdapter` (registered for `ServerType.audiobookshelf`).

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_audiobookshelf.py`:

```python
"""AudiobookshelfAdapter: scan URL/method/Bearer header, classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.audiobookshelf import AudiobookshelfAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://abs.example:13378"
SCAN = f"{BASE}/api/libraries/lib_abc/scan"
ME = f"{BASE}/api/me"


def abs_runtime(
    *, secret: str | None = "tok-secret", retry_attempts: int = 1
) -> ServerRuntime:
    return make_runtime(
        type=ServerType.audiobookshelf,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="lib_abc",
        scan_key="lib:lib_abc",
    )


def test_abs_class_metadata() -> None:
    assert AudiobookshelfAdapter.server_type is ServerType.audiobookshelf
    assert AudiobookshelfAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_abs_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.audiobookshelf) is AudiobookshelfAdapter


async def test_create_adapter_builds_abs(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(abs_runtime(), client)
    assert isinstance(adapter, AudiobookshelfAdapter)
    assert adapter.client is client


@respx.mock
async def test_library_trigger_posts_scan_with_bearer_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(SCAN).mock(return_value=httpx.Response(200))
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(
    client: httpx.AsyncClient, status: int
) -> None:
    respx.post(SCAN).mock(return_value=httpx.Response(status))
    adapter = AudiobookshelfAdapter(abs_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(SCAN).mock(side_effect=httpx.ConnectError("down"))
    adapter = AudiobookshelfAdapter(abs_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_me_with_bearer(client: httpx.AsyncClient) -> None:
    route = respx.get(ME).mock(return_value=httpx.Response(200))
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(ME).mock(return_value=httpx.Response(401))
    adapter = AudiobookshelfAdapter(abs_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_audiobookshelf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.servers.audiobookshelf'`.

- [ ] **Step 3: Implement `servers/audiobookshelf.py`**

Create `mediascanmonitor/servers/audiobookshelf.py`:

```python
"""Audiobookshelf notification adapter (contract §7).

------------------------------------------------------------------------------
AUDIOBOOKSHELF API QUIRKS (kept here so the watcher/pipeline never special-case it):

* Library scan (this endpoint is whole-library; we use library mode only):
    POST {base_url}/api/libraries/{library_id}/scan
  ABS rescans that library asynchronously. The configured ``library_id`` is the
  ABS library id (set in the UI). ``?force=1`` (force a full re-scan of unchanged
  items) is intentionally omitted — the default incremental scan is what a
  file-change notification wants. (ABS also has a path-targeted POST /api/watcher/update,
  but adopting per-folder targeting here is a deferred enhancement — see docs/FOLLOWUPS.md.)

* Auth: Authorization: Bearer {token} HEADER. Never in the URL.

* Success: ABS answers 2xx. We treat any 2xx as ok; the scan runs async.

* test(): GET {base_url}/api/me with the token proves auth + reachability.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the scan path, the /api/me
probe, the Bearer scheme, and the force-flag default against current ABS API docs.
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
class AudiobookshelfAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.audiobookshelf
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})

    def _headers(self) -> dict[str, str]:
        # Bearer token in header only — never in the URL (keeps it out of logs).
        return {"Authorization": f"Bearer {self.server.secret or ''}"}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/libraries/{req.library_id}/scan"
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
                ok=True, status_code=resp.status_code, detail="Audiobookshelf scan triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/me"
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

Modify `mediascanmonitor/servers/__init__.py` — append one line below the existing registration block:

```python
from mediascanmonitor.servers import audiobookshelf as _audiobookshelf  # noqa: F401  (registration side effect)
```

- [ ] **Step 5: Run the ABS tests to verify they pass**

Run: `pytest tests/servers/test_audiobookshelf.py -v`
Expected: PASS — 9 passed (the parametrized `status` test collects 2 items).

- [ ] **Step 6: Lint + type-check the new code**

Run: `ruff check mediascanmonitor/servers/audiobookshelf.py mediascanmonitor/servers/__init__.py tests/servers/test_audiobookshelf.py && mypy mediascanmonitor/servers/audiobookshelf.py mediascanmonitor/servers/__init__.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add tests/servers/test_audiobookshelf.py mediascanmonitor/servers/audiobookshelf.py \
        mediascanmonitor/servers/__init__.py
git commit -m "feat(servers): add Audiobookshelf library-scan adapter with test()"
```

---

### Task 2: Full-suite verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full gate**

Run: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`
Expected: all green; `registry.get_adapter_class(ServerType.audiobookshelf)` resolves to
`AudiobookshelfAdapter`.

- [ ] **Step 2: (If anything is red) fix and re-run**

Address any failure, then re-run Step 1 until green.

---

## Self-Review

**Spec coverage:**

- ABS scan: `POST /api/libraries/{id}/scan`, `Bearer` header — Task 1,
  `test_library_trigger_posts_scan_with_bearer_header`. ✓
- Library-scan only (`supported_scan_modes == {library}`) — Task 1 `test_abs_class_metadata`. ✓
- Self-registration — Task 1 `test_abs_is_registered` + `create_adapter`. ✓
- Secret in header, never in URL — trigger/test tests assert `secret not in str(url)`. ✓
- Success/HTTP-error/transport-error classification — Task 1 trigger tests. ✓
- `test()` auth+reachability via `GET /api/me` — Task 1 `test_test_*`. ✓
- Independent of sub-plan 01; no new dependencies; frozen contract untouched. ✓

**Placeholder scan:** none — complete test + implementation code and exact commands throughout.

**Type consistency:** `AudiobookshelfAdapter` sets `server_type`/`supported_scan_modes` and implements
`trigger(self, req: ScanRequest) -> TriggerResult` / `test(self) -> TestResult` exactly as the frozen
ABC declares; `request_with_retry` call matches the contract signature.

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-20-phase2-02-audiobookshelf-adapter.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session using `executing-plans`, batched with checkpoints.

Which approach?
