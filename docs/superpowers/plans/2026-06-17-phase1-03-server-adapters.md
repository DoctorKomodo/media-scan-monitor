# Phase 1 — Sub-plan 03: Server Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mediascanmonitor/servers/` package — the `ServerAdapter` ABC, a type→class registry, a shared httpx client + tenacity retry helper, and the one concrete Phase 1 adapter (`PlexAdapter`) — so a `ScanRequest` for a Plex server fires a correctly-encoded targeted (or library) partial scan.

**Architecture:** All backend-specific behavior lives behind the `ServerAdapter` ABC (contract section 7). A class-decorator registry maps `ServerType` → adapter class so adding a backend later is one new file + one `@register`. A shared `http.py` owns client construction and a tenacity-based `request_with_retry` (retry on transport errors and 5xx, exponential backoff, never on 4xx) with an injectable async-sleep so tests never actually sleep. `PlexAdapter` is the sole concrete adapter; every Plex quirk (token-in-header, `?path=` encoding, 2xx-means-ok) is confined to `plex.py` — the watcher and pipeline never special-case Plex.

**Tech Stack:** Python 3.14+, `httpx==0.28.1`, `tenacity==9.1.4`, async adapters; tests with `pytest==9.1.0`, `pytest-asyncio==1.4.0` (`asyncio_mode=auto`), `respx==0.23.1`. `mypy --strict` clean, `ruff` clean, line length 100, no `from __future__ import annotations` (PEP 649 default on 3.14; forward refs unquoted).

---

## Prerequisites (hard dependency — do not start until merged)

This sub-plan **consumes** frozen contract names from sub-plans 01 and 02. The following must already exist and import cleanly (per the forward-only dependency graph `01 → 02 → 03`):

- `mediascanmonitor/db/models.py`: enums `ServerType`, `ScanMode`, `DebounceMode` (contract §1).
- `mediascanmonitor/pipeline/events.py`: `FsEventType`, `ScanRequest` (contract §5).
- `mediascanmonitor/config/runtime.py`: `ServerRuntime` (contract §6).

This plan **does not** redefine, rename, or re-import-with-fallback any of those. If they are missing, stop — sub-plans 01/02 are not done.

**Contract deviations in this plan: none.** All public names and signatures are taken verbatim from contract §7.

---

## File structure (what this plan builds)

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/servers/base.py` | `TriggerResult`, `TestResult` (frozen slotted dataclasses); `ServerAdapter` ABC. |
| `mediascanmonitor/servers/registry.py` | `register` decorator, `get_adapter_class`, `create_adapter`; unknown type → `ValueError`. |
| `mediascanmonitor/servers/http.py` | `build_client(...)`, `request_with_retry(...)` (tenacity; retry on `httpx.TransportError` + 5xx; exp backoff; injectable async sleep). |
| `mediascanmonitor/servers/plex.py` | `PlexAdapter` (`@register`); targeted/library `trigger`; `/identity` `test`. Plex quirks documented inline. |
| `mediascanmonitor/servers/__init__.py` | (modify) import `plex` so `PlexAdapter` self-registers on package import. |
| `tests/servers/__init__.py` | test package marker. |
| `tests/servers/conftest.py` | typed `make_plex_runtime` / `make_scan_request` builders, `client` fixture, `clean_registry` fixture. |
| `tests/servers/test_base.py` | dataclass immutability/slots + ABC shape. |
| `tests/servers/test_registry.py` | register/lookup/create mechanics + unknown-type error. |
| `tests/servers/test_http.py` | client construction + retry/backoff classification (sleep mocked). |
| `tests/servers/test_plex.py` | URL/encoding/header assertions, success/failure classification, transport error, `test()`, registry integration. |

**How retry/sleep is mocked in tests (read before Task 3):** `http.py` defines a module-level async function `_async_sleep` and passes it to tenacity's `AsyncRetrying(sleep=...)`. Because `request_with_retry` looks up the module global `_async_sleep` *at call time* (when it constructs the `AsyncRetrying`), a test can `monkeypatch.setattr(http, "_async_sleep", instant)` to replace the sleep with an immediate no-op. Tests then assert retry happened by counting the mocked HTTP calls (`route.call_count`) rather than by observing wall-clock delay. Adapter tests avoid sleeping entirely by using `retry_attempts=1` (no retries), so only `test_http.py` patches the sleep.

---

### Task 1: Adapter base types (`servers/base.py`) + test scaffolding

**Files:**
- Create: `tests/servers/__init__.py`
- Create: `tests/servers/conftest.py`
- Create: `tests/servers/test_base.py`
- Create: `mediascanmonitor/servers/base.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/servers/__init__.py`:

```python
"""Tests for mediascanmonitor.servers (Phase 1, sub-plan 03)."""
```

- [ ] **Step 2: Create shared test fixtures/builders**

Create `tests/servers/conftest.py`:

```python
"""Shared builders and fixtures for the server-adapter tests.

The builders construct the FROZEN contract types (ServerRuntime / ScanRequest)
with full keyword signatures so mypy --strict stays happy (no **dict splatting).
"""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest


def make_plex_runtime(
    *,
    server_id: int = 1,
    name: str = "My Plex",
    type: ServerType = ServerType.plex,
    base_url: str = "https://plex.example:32400",
    verify_tls: bool = True,
    timeout_seconds: float = 10.0,
    secret: str | None = "tok-secret",
    scan_mode: ScanMode = ScanMode.targeted,
    debounce_mode: DebounceMode = DebounceMode.trailing,
    debounce_window_seconds: int = 30,
    retry_attempts: int = 1,
    webhook_method: str | None = None,
    webhook_headers_json: str | None = None,
    webhook_body_template: str | None = None,
) -> ServerRuntime:
    """Build a ServerRuntime; defaults to an enabled Plex server with no retries."""
    return ServerRuntime(
        server_id=server_id,
        name=name,
        type=type,
        base_url=base_url,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
        secret=secret,
        scan_mode=scan_mode,
        debounce_mode=debounce_mode,
        debounce_window_seconds=debounce_window_seconds,
        retry_attempts=retry_attempts,
        webhook_method=webhook_method,
        webhook_headers_json=webhook_headers_json,
        webhook_body_template=webhook_body_template,
    )


def make_scan_request(
    *,
    server_id: int = 1,
    server_name: str = "My Plex",
    scan_mode: ScanMode = ScanMode.targeted,
    scan_path: str | None = "/data/media/tvseries/Tom & Jerry",
    library_id: str | None = "2",
    scan_key: str = "/data/media/tvseries/Tom & Jerry",
    event_type: FsEventType = FsEventType.created,
    file_path: str = "/data/media/tvseries/Tom & Jerry/ep01.mkv",
    top_folder: str | None = "Tom & Jerry",
) -> ScanRequest:
    """Build a ScanRequest; defaults to a targeted Plex scan with a space + '&' in the path."""
    return ScanRequest(
        server_id=server_id,
        server_name=server_name,
        scan_mode=scan_mode,
        scan_path=scan_path,
        library_id=library_id,
        scan_key=scan_key,
        event_type=event_type,
        file_path=file_path,
        top_folder=top_folder,
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """A real httpx.AsyncClient; respx patches its transport when @respx.mock is active."""
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def clean_registry() -> AsyncIterator[None]:
    """Snapshot and restore the adapter registry so tests that register dummies don't leak."""
    from mediascanmonitor.servers import registry

    saved = dict(registry._REGISTRY)
    try:
        yield None
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)
```

- [ ] **Step 3: Write the failing test for base types**

Create `tests/servers/test_base.py`:

```python
"""Shape tests for the ServerAdapter ABC and result dataclasses."""

import dataclasses

import httpx
import pytest

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult

from .conftest import make_plex_runtime


def test_trigger_result_is_frozen_and_slotted() -> None:
    r = TriggerResult(ok=True, status_code=200, detail="ok")
    assert (r.ok, r.status_code, r.detail) == (True, 200, "ok")
    assert not hasattr(r, "__dict__")  # slots=True
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = False  # type: ignore[misc]


def test_test_result_is_frozen_and_slotted() -> None:
    r = TestResult(ok=False, detail="nope")
    assert (r.ok, r.detail) == (False, "nope")
    assert not hasattr(r, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.detail = "changed"  # type: ignore[misc]


def test_serveradapter_declares_the_two_abstract_methods() -> None:
    assert ServerAdapter.__abstractmethods__ == frozenset({"trigger", "test"})
    assert getattr(ServerAdapter.trigger, "__isabstractmethod__", False)
    assert getattr(ServerAdapter.test, "__isabstractmethod__", False)


async def test_concrete_subclass_stores_server_and_client(
    client: httpx.AsyncClient,
) -> None:
    class _Dummy(ServerAdapter):
        server_type = ServerType.plex
        supported_scan_modes = frozenset({ScanMode.targeted})

        async def trigger(self, req: ScanRequest) -> TriggerResult:
            return TriggerResult(ok=True, status_code=200, detail="ok")

        async def test(self) -> TestResult:
            return TestResult(ok=True, detail="ok")

    runtime = make_plex_runtime()
    adapter = _Dummy(runtime, client)
    assert adapter.server is runtime
    assert adapter.client is client
    assert _Dummy.supported_scan_modes == frozenset({ScanMode.targeted})
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `pytest tests/servers/test_base.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'mediascanmonitor.servers.base'` (the module does not exist yet).

- [ ] **Step 5: Implement `servers/base.py`**

Create `mediascanmonitor/servers/base.py`:

```python
"""The ServerAdapter ABC and its result value objects (contract §7).

A "server" is a notification target (Plex, Emby, ...). Every backend-specific
detail lives in a concrete adapter; the watcher and pipeline only ever see this
ABC and the two result dataclasses below.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import httpx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest


@dataclass(frozen=True, slots=True)
class TriggerResult:
    """Outcome of a single trigger() call. ``ok`` is True only for a 2xx response."""

    ok: bool
    status_code: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class TestResult:
    """Outcome of a connectivity/auth probe (test())."""

    ok: bool
    detail: str


class ServerAdapter(ABC):
    """Base class for every notification target.

    Subclasses MUST set the two ClassVars and implement the two async methods.
    They receive an immutable ``ServerRuntime`` (decrypted secret in memory) and a
    shared ``httpx.AsyncClient`` owned by the engine.
    """

    server_type: ClassVar[ServerType]
    supported_scan_modes: ClassVar[frozenset[ScanMode]]

    def __init__(self, server: ServerRuntime, client: httpx.AsyncClient) -> None:
        self.server = server
        self.client = client

    @abstractmethod
    async def trigger(self, req: ScanRequest) -> TriggerResult:
        """Fire the backend's scan/refresh for ``req``."""

    @abstractmethod
    async def test(self) -> TestResult:
        """Probe auth + reachability only (no scan)."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/servers/test_base.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 7: Lint + type-check the new module**

Run: `ruff check mediascanmonitor/servers/base.py tests/servers/ && mypy mediascanmonitor/servers/base.py`
Expected: ruff reports "All checks passed!"; mypy reports "Success: no issues found".

- [ ] **Step 8: Commit**

```bash
git add tests/servers/__init__.py tests/servers/conftest.py tests/servers/test_base.py \
        mediascanmonitor/servers/base.py
git commit -m "feat(servers): add ServerAdapter ABC and result dataclasses"
```

---

### Task 2: Adapter registry (`servers/registry.py`)

**Files:**
- Create: `tests/servers/test_registry.py`
- Create: `mediascanmonitor/servers/registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_registry.py`:

```python
"""Registry mechanics: register / get_adapter_class / create_adapter / unknown error."""

import httpx
import pytest

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult

from .conftest import make_plex_runtime


def _make_dummy_cls() -> type[ServerAdapter]:
    class _WebhookDummy(ServerAdapter):
        server_type = ServerType.webhook
        supported_scan_modes = frozenset({ScanMode.library})

        async def trigger(self, req: ScanRequest) -> TriggerResult:
            return TriggerResult(ok=True, status_code=200, detail="ok")

        async def test(self) -> TestResult:
            return TestResult(ok=True, detail="ok")

    return _WebhookDummy


def test_register_returns_the_class_and_indexes_it(clean_registry: None) -> None:
    cls = _make_dummy_cls()
    returned = registry.register(cls)
    assert returned is cls
    assert registry.get_adapter_class(ServerType.webhook) is cls


async def test_create_adapter_instantiates_the_registered_class(
    clean_registry: None, client: httpx.AsyncClient
) -> None:
    cls = registry.register(_make_dummy_cls())
    runtime = make_plex_runtime(type=ServerType.webhook)
    adapter = registry.create_adapter(runtime, client)
    assert isinstance(adapter, cls)
    assert adapter.server is runtime
    assert adapter.client is client


def test_get_adapter_class_unknown_type_raises_value_error(clean_registry: None) -> None:
    registry._REGISTRY.pop(ServerType.emby, None)
    with pytest.raises(ValueError) as exc:
        registry.get_adapter_class(ServerType.emby)
    assert "emby" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_registry.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'mediascanmonitor.servers.registry'` is not raised here because `conftest.clean_registry` imports it lazily; instead the import `from mediascanmonitor.servers import registry` at the top fails with `ImportError` (registry.py does not exist yet).

- [ ] **Step 3: Implement `servers/registry.py`**

Create `mediascanmonitor/servers/registry.py`:

```python
"""Type → adapter-class registry (contract §7).

Adding a backend = define a ServerAdapter subclass in its own module and decorate
it with @register. Nothing else in the codebase needs to learn the new type.
"""

import httpx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ServerType
from mediascanmonitor.servers.base import ServerAdapter

_REGISTRY: dict[ServerType, type[ServerAdapter]] = {}


def register(cls: type[ServerAdapter]) -> type[ServerAdapter]:
    """Class decorator: index ``cls`` under its ``server_type``. Returns ``cls`` unchanged."""
    _REGISTRY[cls.server_type] = cls
    return cls


def get_adapter_class(server_type: ServerType) -> type[ServerAdapter]:
    """Return the adapter class for ``server_type`` or raise a clear ValueError."""
    try:
        return _REGISTRY[server_type]
    except KeyError:
        known = ", ".join(sorted(t.value for t in _REGISTRY)) or "(none registered)"
        raise ValueError(
            f"No server adapter registered for type {server_type.value!r}; known: {known}"
        ) from None


def create_adapter(server: ServerRuntime, client: httpx.AsyncClient) -> ServerAdapter:
    """Build the adapter instance for ``server`` using the shared ``client``."""
    return get_adapter_class(server.type)(server, client)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/servers/test_registry.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/servers/registry.py tests/servers/test_registry.py && mypy mediascanmonitor/servers/registry.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add tests/servers/test_registry.py mediascanmonitor/servers/registry.py
git commit -m "feat(servers): add adapter registry with clear unknown-type error"
```

---

### Task 3: Shared HTTP client + retry helper (`servers/http.py`)

**Files:**
- Create: `tests/servers/test_http.py`
- Create: `mediascanmonitor/servers/http.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_http.py`:

```python
"""build_client + request_with_retry classification (sleep mocked so tests are instant).

Mocking approach: request_with_retry passes the module-level ``http._async_sleep`` to
tenacity's AsyncRetrying as its ``sleep`` callable, and looks that global up at call
time. Patching ``http._async_sleep`` therefore replaces the backoff sleep with an
instant no-op; we then prove retries happened by counting respx calls, not by timing.
"""

import httpx
import pytest
import respx

from mediascanmonitor.servers import http

URL = "https://backend.example/library/sections/2/refresh"


async def _instant(_seconds: float) -> None:
    return None


async def test_build_client_applies_timeout() -> None:
    client = http.build_client(verify_tls=True, timeout_seconds=7.5)
    try:
        assert isinstance(client, httpx.AsyncClient)
        assert client.timeout.connect == 7.5
        assert client.timeout.read == 7.5
    finally:
        await client.aclose()


async def test_build_client_accepts_verify_false() -> None:
    client = http.build_client(verify_tls=False, timeout_seconds=3.0)
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()


@respx.mock
async def test_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200)]
    )
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_attempts_and_returns_last_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 503
    assert route.call_count == 3


@respx.mock
async def test_does_not_retry_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_retries_transport_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200)]
    )
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_transport_error_propagates_after_exhausting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.TransportError):
            await http.request_with_retry(client, "GET", URL, attempts=2)
    assert route.call_count == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_http.py -v`
Expected: FAIL — `ImportError`/`AttributeError` (the `http` module / its functions do not exist yet).

- [ ] **Step 3: Implement `servers/http.py`**

Create `mediascanmonitor/servers/http.py`:

```python
"""Shared httpx client construction and a tenacity-based retry helper (contract §7).

Retry policy: retry on transport errors (DNS/connect/read failures) and on 5xx
responses; never retry 4xx. Exponential backoff between attempts. The sleep used
between attempts is the module-level ``_async_sleep`` so tests can patch it to a
no-op (see tests/servers/test_http.py).
"""

import asyncio
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def build_client(*, verify_tls: bool, timeout_seconds: float) -> httpx.AsyncClient:
    """Construct the shared async client (one per server, owned by the engine)."""
    return httpx.AsyncClient(verify=verify_tls, timeout=timeout_seconds)


async def _async_sleep(seconds: float) -> None:  # pragma: no cover - patched in tests
    """Backoff sleep; patched to a no-op in tests via monkeypatch on this attribute."""
    await asyncio.sleep(seconds)


class _RetryableStatus(Exception):
    """Internal marker carrying a 5xx response so tenacity can retry on it."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"server error {response.status_code}")
        self.response = response


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TransportError, _RetryableStatus))


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int,
    **kwargs: Any,
) -> httpx.Response:
    """Issue ``method url`` with retry on transport errors + 5xx (exp backoff).

    On give-up after a 5xx, returns the last response (so callers classify it as a
    failure). On give-up after a transport error, the httpx exception propagates.
    4xx responses are returned immediately without retry.
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=0.2, max=5.0),
        retry=retry_if_exception(_is_retryable),
        sleep=_async_sleep,
        reraise=True,
    )
    try:
        async for attempt in retrying:
            with attempt:
                response = await client.request(method, url, **kwargs)
                if 500 <= response.status_code < 600:
                    raise _RetryableStatus(response)
                return response
    except _RetryableStatus as exc:
        return exc.response
    raise AssertionError("unreachable")  # pragma: no cover
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/servers/test_http.py -v`
Expected: PASS — 7 passed. (Runs near-instantly because `_async_sleep` is patched.)

- [ ] **Step 5: Lint + type-check**

Run: `ruff check mediascanmonitor/servers/http.py tests/servers/test_http.py && mypy mediascanmonitor/servers/http.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add tests/servers/test_http.py mediascanmonitor/servers/http.py
git commit -m "feat(servers): add shared httpx client and tenacity retry helper"
```

---

### Task 4: Plex adapter (`servers/plex.py`) + self-registration

**Files:**
- Create: `tests/servers/test_plex.py`
- Create: `mediascanmonitor/servers/plex.py`
- Modify: `mediascanmonitor/servers/__init__.py` (import `plex` so it self-registers)

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_plex.py`:

```python
"""PlexAdapter: exact URL/encoding/header, success/failure classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.plex import PlexAdapter

from .conftest import make_plex_runtime, make_scan_request

BASE = "https://plex.example:32400"
REFRESH = f"{BASE}/library/sections/2/refresh"
IDENTITY = f"{BASE}/identity"


def test_plex_class_metadata() -> None:
    assert PlexAdapter.server_type is ServerType.plex
    assert PlexAdapter.supported_scan_modes == frozenset(
        {ScanMode.targeted, ScanMode.library}
    )


def test_plex_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.plex) is PlexAdapter


async def test_create_adapter_builds_plex(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(make_plex_runtime(), client)
    assert isinstance(adapter, PlexAdapter)
    assert adapter.client is client


@respx.mock
async def test_targeted_trigger_encodes_path_and_sends_token_in_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(secret="tok-secret"), client)
    req = make_scan_request(
        scan_mode=ScanMode.targeted,
        scan_path="/data/media/tvseries/Tom & Jerry",
        library_id="2",
    )

    res = await adapter.trigger(req)

    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1

    request = route.calls.last.request
    assert request.method == "GET"
    # token is in the HEADER, never in the URL/query
    assert request.headers["X-Plex-Token"] == "tok-secret"
    assert "X-Plex-Token" not in str(request.url)
    assert "tok-secret" not in str(request.url)
    # path query param: decoded round-trips, and the raw URL shows %20 and %26 encoding
    assert request.url.params["path"] == "/data/media/tvseries/Tom & Jerry"
    assert "path=/data/media/tvseries/Tom%20%26%20Jerry" in str(request.url)


@respx.mock
async def test_library_trigger_has_no_path_param(client: httpx.AsyncClient) -> None:
    route = respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(), client)
    req = make_scan_request(
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="2",
        scan_key="lib:2",
    )

    res = await adapter.trigger(req)

    assert res.ok is True
    assert res.status_code == 200
    request = route.calls.last.request
    assert "path" not in request.url.params
    assert request.url.query == b""
    assert request.headers["X-Plex-Token"] == "tok-secret"


@respx.mock
async def test_trigger_success_classification(client: httpx.AsyncClient) -> None:
    respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.trigger(make_scan_request())
    assert res == res.__class__(ok=True, status_code=200, detail=res.detail)
    assert res.ok is True and res.status_code == 200


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(
    client: httpx.AsyncClient, status: int
) -> None:
    respx.get(REFRESH).mock(return_value=httpx.Response(status))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.trigger(make_scan_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = PlexAdapter(make_plex_runtime(retry_attempts=1), client)
    res = await adapter.trigger(make_scan_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_identity_with_token(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(IDENTITY).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["X-Plex-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(IDENTITY).mock(return_value=httpx.Response(401))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_plex.py -v`
Expected: FAIL — `ImportError: cannot import name 'PlexAdapter'` / `ModuleNotFoundError: No module named 'mediascanmonitor.servers.plex'`.

- [ ] **Step 3: Implement `servers/plex.py`**

Create `mediascanmonitor/servers/plex.py`:

```python
"""Plex notification adapter (contract §7).

------------------------------------------------------------------------------
PLEX API QUIRKS (kept here so the watcher/pipeline never special-case Plex):

* Partial (targeted) scan:
    GET {base_url}/library/sections/{library_id}/refresh?path={url-encoded path}
  Plex matches ``path`` against the on-disk library path and rescans only that
  subtree. We URL-encode with ``safe="/"`` so the path separators stay literal
  (Plex expects a real path, not a fully percent-escaped blob); spaces become
  %20, ampersands %26, etc.

* Whole-library scan: the same URL WITHOUT ``?path=``.

* Auth: the token goes in the ``X-Plex-Token`` HEADER. Plex also accepts it as a
  query param, but we never put it in the URL so it cannot leak into logs.

* Success: Plex answers 2xx (usually 200, empty body) and scans asynchronously.
  We treat any 2xx as ok; there is no per-item completion signal to await.
------------------------------------------------------------------------------
"""

from typing import ClassVar
from urllib.parse import quote

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


@register
class PlexAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.plex
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset(
        {ScanMode.targeted, ScanMode.library}
    )

    def _headers(self) -> dict[str, str]:
        # Token in header only — never in the URL (keeps it out of logs).
        return {"X-Plex-Token": self.server.secret or ""}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/library/sections/{req.library_id}/refresh"
        if req.scan_mode is ScanMode.targeted and req.scan_path is not None:
            url = f"{url}?path={quote(req.scan_path, safe='/')}"
        try:
            resp = await request_with_retry(
                self.client,
                "GET",
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
                ok=True, status_code=resp.status_code, detail="Plex scan triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/identity"
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

Modify `mediascanmonitor/servers/__init__.py` — append below the existing docstring so `PlexAdapter` registers whenever the package is imported (the engine imports `mediascanmonitor.servers`):

```python
# Importing the concrete adapter modules triggers their @register decorators so
# create_adapter() can find them. Add one line here per new server type.
from mediascanmonitor.servers import plex as _plex  # noqa: F401  (registration side effect)
```

(Keep the existing module docstring at the top of the file; this block goes after it.)

- [ ] **Step 5: Run the Plex tests to verify they pass**

Run: `pytest tests/servers/test_plex.py -v`
Expected: PASS — 11 passed (the two `status` params count as 2).

- [ ] **Step 6: Lint + type-check the new code**

Run: `ruff check mediascanmonitor/servers/plex.py mediascanmonitor/servers/__init__.py tests/servers/test_plex.py && mypy mediascanmonitor/servers/plex.py mediascanmonitor/servers/__init__.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add tests/servers/test_plex.py mediascanmonitor/servers/plex.py \
        mediascanmonitor/servers/__init__.py
git commit -m "feat(servers): add Plex adapter with targeted/library scans and test()"
```

---

### Task 5: Full-suite verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the whole server test package**

Run: `pytest tests/servers/ -v`
Expected: PASS — 25 passed (4 base + 3 registry + 7 http + 11 plex).

- [ ] **Step 2: Run the entire test suite (no regressions in the CLI smoke tests)**

Run: `pytest`
Expected: PASS — all tests pass (including the pre-existing `tests/test_cli.py`).

- [ ] **Step 3: Type-check the whole servers package strictly**

Run: `mypy mediascanmonitor/servers/`
Expected: "Success: no issues found in 5 source files".

- [ ] **Step 4: Lint + format-check the package and its tests**

Run: `ruff check mediascanmonitor/servers/ tests/servers/ && ruff format --check mediascanmonitor/servers/ tests/servers/`
Expected: "All checks passed!" and the format check reports nothing to reformat.

- [ ] **Step 5: Commit (only if Steps 1–4 surfaced fixes)**

```bash
git add -A
git commit -m "test(servers): verify full adapter suite, types, and lint clean"
```

---

## Self-Review

**1. Spec coverage** (against the task brief and contract §7):

- `base.py` `TriggerResult`/`TestResult` (frozen, slotted) + `ServerAdapter` ABC with ClassVars and abstract async `trigger`/`test` → Task 1. ✓
- `registry.py` `register` / `get_adapter_class` / `create_adapter`; unknown type raises a clear error → Task 2. ✓
- `http.py` `build_client(verify_tls, timeout_seconds)` + `request_with_retry(... attempts ...)` using tenacity (retry on `httpx.TransportError` + 5xx, exp backoff, no retry on 4xx), mockable sleep → Task 3. ✓
- `plex.py` `PlexAdapter`, registered, `server_type=plex`, `supported_scan_modes={targeted, library}`; targeted vs library URL; `X-Plex-Token` header; `/identity` test; 2xx⇒ok → Task 4. ✓
- Required tests: targeted exact URL + encoded `path=` (space + `&`), token-in-header-not-URL, library no-`path`, 200⇒ok / 401&404⇒not-ok / transport⇒not-ok, retry-on-503-then-success with call count, give-up-after-attempts, no-retry-on-404, registry get/create + unknown raises, `test()` happy + 401 → Tasks 2–4. ✓
- Constraints: no `from __future__ import annotations` (PEP 649; forward refs unquoted); mypy --strict & ruff gates per task + Task 5; line length 100; async adapters; Plex quirks documented inline; pipeline/watcher never special-case Plex (stated in Architecture + plex.py docstring). ✓
- No new deps: only `httpx`, `tenacity` (runtime) and `pytest`/`pytest-asyncio`/`respx` (dev), all pinned in `pyproject.toml`. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to"/"write tests for the above" — every code and test step contains complete, runnable content. ✓

**3. Type consistency:** `TriggerResult(ok, status_code, detail)` and `TestResult(ok, detail)` used identically everywhere; `request_with_retry(client, method, url, *, attempts, **kwargs)` called consistently in `plex.py` and tests; `registry.register/get_adapter_class/create_adapter` and the private `registry._REGISTRY` used consistently across `registry.py`, `conftest.py`, `test_registry.py`, `test_plex.py`; `http._async_sleep` is the single patched symbol named the same in `http.py` and `test_http.py`; `make_plex_runtime`/`make_scan_request` signatures match the frozen `ServerRuntime`/`ScanRequest` field names from contract §6/§5. ✓

A redundant `result = adapter.__class__` line that crept into `test_targeted_trigger_...` was found and removed inline.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-phase1-03-server-adapters.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
</content>
</invoke>
