# Phase 1 — Sub-plan 06: Engine, CLI & Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 1 building blocks (DB/repo, runtime config, watcher, adapters, pipeline) into a single async `Engine` with live `rebuild()`, structured logging, and a headless `run --no-web` CLI.

**Architecture:** One async process, one event loop. `Engine.start()` builds an immutable `RuntimeConfig` from the DB (off-loop via `asyncio.to_thread`), constructs one HTTP client + adapter per enabled server, builds the `Dispatcher`/`Debouncer`, sets the watcher roots, then consumes `watcher.events()`: every `FsEvent` is `route()`d into zero-or-more `ScanRequest`s and `debouncer.submit()`ed. `Engine.rebuild()` rebuilds the snapshot and swaps adapters, routing snapshot, server map, and watch roots **synchronously** (no `await` between the swaps) so no event is dropped and no restart is needed. The CLI's headless body is a testable coroutine (`serve_headless`) that accepts an injected watcher and stop event, so tests never touch real signals, sleeps, or sockets.

**Tech Stack:** Python 3.14+, asyncio, `structlog==26.1.0`, `httpx==0.28.1`, `pytest==9.1.0` + `pytest-asyncio==1.4.0` (`asyncio_mode = "auto"`). No new dependencies.

---

## Scope & boundaries

This sub-plan owns exactly three production modules:

- `mediascanmonitor/observ/logging.py` — `configure_logging(...)`.
- `mediascanmonitor/engine.py` — the `Engine` class.
- `mediascanmonitor/cli.py` — extend the existing `run` command (implement `--no-web`).

It **consumes** (never redefines) the frozen contract names below. Everything in sub-plans 01–05 is assumed present and correct when this plan executes (forward dependency order: `06` depends on everything).

### Consumed interfaces (frozen — copy, never rename)

From `docs/superpowers/plans/2026-06-17-phase1-00-interface-contract.md`:

```python
# §1  db/models.py  (enums)
class ServerType(str, Enum): webhook="webhook"; plex="plex"; emby="emby"; jellyfin="jellyfin"; audiobookshelf="audiobookshelf"
class ScanMode(str, Enum):   targeted="targeted"; library="library"
class DebounceMode(str, Enum): off="off"; trailing="trailing"

# §3  db/crypto.py
class SecretBox:
    def __init__(self, key: bytes) -> None: ...
def load_or_create_key(path: Path, env_key: str | None = None) -> bytes: ...

# §4  db/repo.py
class Repo:
    def __init__(self, session_factory: Callable[[], Session], box: SecretBox) -> None: ...

# §5  pipeline/events.py
class FsEventType(str, Enum): created="created"; moved_to="moved_to"; deleted="deleted"; moved_from="moved_from"
@dataclass(frozen=True, slots=True)
class FsEvent: path: str; event_type: FsEventType; is_dir: bool
@dataclass(frozen=True, slots=True)
class ScanRequest:
    server_id: int; server_name: str; scan_mode: ScanMode
    scan_path: str | None; library_id: str | None; scan_key: str
    event_type: FsEventType; file_path: str; top_folder: str | None

# §6  config/runtime.py
@dataclass(frozen=True, slots=True)
class ServerRuntime:  # server_id,name,type,base_url,verify_tls,timeout_seconds,secret,
                      # scan_mode,debounce_mode,debounce_window_seconds,retry_attempts,
                      # webhook_method,webhook_headers_json,webhook_body_template
@dataclass(frozen=True, slots=True)
class FolderRoute:    # server_id,server_name,path,extensions(frozenset),library_id,scan_mode
@dataclass(frozen=True, slots=True)
class RuntimeConfig:  # watch_paths(frozenset[str]),routes(tuple),servers(dict[int,ServerRuntime]),ignore_dirs(frozenset[str])
def build_runtime_config(repo: Repo) -> RuntimeConfig: ...

# §7  servers/
class ServerAdapter(ABC):
    server_type: ClassVar[ServerType]; supported_scan_modes: ClassVar[frozenset[ScanMode]]
    def __init__(self, server: ServerRuntime, client: httpx.AsyncClient) -> None: ...
    async def trigger(self, req: ScanRequest) -> TriggerResult: ...
    async def test(self) -> TestResult: ...
@dataclass(frozen=True, slots=True)
class TriggerResult: ok: bool; status_code: int | None; detail: str
@dataclass(frozen=True, slots=True)
class TestResult: ok: bool; detail: str
def create_adapter(server: ServerRuntime, client: httpx.AsyncClient) -> ServerAdapter: ...   # servers/registry.py
def build_client(*, verify_tls: bool, timeout_seconds: float) -> httpx.AsyncClient: ...        # servers/http.py

# §8  watcher/
class WatcherBackend(Protocol):
    def set_roots(self, roots: set[str]) -> None: ...
    def events(self) -> AsyncIterator[FsEvent]: ...
    async def aclose(self) -> None: ...
class InotifyBackend:  # watcher/inotify_backend.py — __init__(ignore_dirs: frozenset[str])

# §9  pipeline/
def route(event: FsEvent, config: RuntimeConfig) -> list[ScanRequest]: ...   # pipeline/router.py
class Debouncer:                                                              # pipeline/debounce.py
    def __init__(self, dispatch: Callable[[ScanRequest], Awaitable[None]],
                 servers: dict[int, ServerRuntime], *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None: ...
    async def submit(self, req: ScanRequest) -> None: ...
    async def aclose(self) -> None: ...
class Dispatcher:                                                            # pipeline/dispatcher.py
    def __init__(self, adapters: dict[int, ServerAdapter]) -> None: ...
    async def dispatch(self, req: ScanRequest) -> TriggerResult: ...
    def set_adapters(self, adapters: dict[int, ServerAdapter]) -> None: ...
```

**One assumed signature owned by sub-plan 01 (`db/session.py`)**, used only inside the CLI's real-startup path: `def init_db(db_path: Path) -> Callable[[], Session]:` — runs `create_all` + migrate and returns a `Session` factory. This plan references it in `cli._build_repo()` only; that path is exercised by sub-plan 01's tests and Phase-1 e2e, not unit-tested here (the CLI's testable surface injects the repo).

### Design decisions specific to this sub-plan (not deviations — contract is silent on these)

1. **One `httpx.AsyncClient` per enabled server.** `build_client` takes per-server `verify_tls`/`timeout_seconds`, so the engine builds one client per server and passes it to `create_adapter`. The engine owns these clients and closes them in `aclose()` and on `rebuild()` (old clients closed after the swap).
2. **`Dispatcher.dispatch` returns `TriggerResult`, but `Debouncer` wants `Callable[[ScanRequest], Awaitable[None]]`.** The engine passes a thin `self._dispatch` wrapper that awaits `dispatcher.dispatch(req)` and returns `None` (keeps `mypy --strict` happy; never special-cases a backend).
3. **`rebuild()` does NOT recreate the `Debouncer`** (the contract's rebuild list is: `set_roots`, rebuild adapters, `dispatcher.set_adapters`, swap routing snapshot — debouncer is not listed). To still honor live server add/remove and changed debounce policy *without* dropping pending timers, the engine constructs the `Debouncer` once with a **reference to an engine-owned `dict[int, ServerRuntime]`** and updates that dict **in place** during the atomic swap. The same single `Dispatcher` instance is reused (only its adapter map is swapped). This means a *burst already mid-window* keeps firing on the same debouncer — nothing is cancelled or dropped.
4. **Atomicity = no `await` between swaps.** `build_runtime_config` (the only pre-swap `await`, off-loop via `to_thread`) and old-client `aclose()` (after the swap) bracket a fully synchronous swap block: `dispatcher.set_adapters` → in-place `servers` update → `self._config = new` reference reassignment → `watcher.set_roots`. `_handle_event` snapshots `self._config` into a local before any `await`, so each in-flight event routes against one consistent snapshot.
5. **Logging redaction processor.** `configure_logging` installs a `_redact_secrets` processor that masks values for a fixed set of sensitive keys (`token`, `secret`, `password`, `api_key`, `authorization`, `x_plex_token`). This makes "secrets are not emitted" a real, testable invariant (CLAUDE rule 5) rather than a hope.
6. **`run` without `--no-web` prints a clear Phase-3 message to stderr and returns exit code `2`** (the feature is unavailable, not a crash) — never a stack trace. `run --no-web` returns `0` on clean shutdown.

---

## File structure

| File | Responsibility |
|---|---|
| `mediascanmonitor/observ/logging.py` (create) | `configure_logging(*, json_logs, level)` + `_redact_secrets` processor. |
| `mediascanmonitor/engine.py` (create) | `Engine`: lifecycle (`start`/`rebuild`/`aclose`), wiring, atomic swap. |
| `mediascanmonitor/cli.py` (modify) | `serve_headless(...)` testable coroutine + `_build_repo()` + `_cmd_run(args)`; dispatch `run`. |
| `tests/_helpers.py` (create) | Shared test doubles: `RecordingAdapter`, `FakeClient`, config builders, `wait_for`, and a re-export of the canonical `FakeWatcher` from `mediascanmonitor.watcher.base` (sub-plan 04). Not collected (no `test_` prefix). |
| `tests/test_logging.py` (create) | Logging smoke + redaction tests. |
| `tests/test_engine.py` (create) | Engine wiring + rebuild + atomicity + shutdown tests. |
| `tests/test_cli.py` (modify) | Revise the Phase-1 stub test; add `serve_headless` + wiring tests. |

---

## Task 1: Structured logging (`observ/logging.py`)

**Files:**
- Create: `mediascanmonitor/observ/logging.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logging.py`:

```python
"""Tests for structlog configuration and secret redaction (sub-plan 06)."""

from __future__ import annotations

import json

import pytest
import structlog
from mediascanmonitor.observ.logging import _redact_secrets, configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()


def test_configure_logging_runs_without_error() -> None:
    configure_logging(json_logs=True, level="INFO")
    log = structlog.get_logger("smoke")
    # Must not raise for any standard level call.
    log.info("started", watch_paths=3)
    log.warning("slow")
    log.error("boom", detail="x")


def test_redact_secrets_masks_sensitive_keys() -> None:
    event = {
        "event": "trigger",
        "token": "PLEX-SECRET-123",
        "Authorization": "Bearer abc",
        "api_key": "k",
        "scan_path": "/data/tv/Shoresy",
    }
    out = _redact_secrets(None, "info", event)
    assert out["token"] == "***"
    assert out["Authorization"] == "***"  # case-insensitive key match
    assert out["api_key"] == "***"
    assert out["scan_path"] == "/data/tv/Shoresy"  # non-sensitive untouched
    assert out["event"] == "trigger"


def test_json_output_redacts_secret_value(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True, level="INFO")
    structlog.get_logger("redact").info("trigger", token="PLEX-SECRET-123")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "trigger"
    assert payload["token"] == "***"
    assert "PLEX-SECRET-123" not in line


def test_level_filtering_drops_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True, level="WARNING")
    log = structlog.get_logger("filter")
    log.info("hidden")
    log.warning("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.observ.logging'`.

- [ ] **Step 3: Implement `observ/logging.py`**

Create `mediascanmonitor/observ/logging.py`:

```python
"""Structured (structlog) logging configuration.

`configure_logging` is called once at process start. It installs a small,
fixed processor chain that renders either JSON (production/Docker) or a
human-friendly console format, and a redaction processor that masks values
for a fixed set of sensitive keys so secrets never reach the log sink
(CLAUDE rule 5: "never log secrets").
"""

from __future__ import annotations

import logging

import structlog
from structlog.typing import EventDict, WrappedLogger

# Keys whose VALUES must never be emitted. Matched case-insensitively.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"token", "secret", "password", "api_key", "authorization", "x_plex_token"}
)
_REDACTED = "***"


def _redact_secrets(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: mask values for known sensitive keys in place."""
    for key in list(event_dict):
        if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, json_logs: bool = True, level: str = "INFO") -> None:
    """Configure structlog process-wide.

    Args:
        json_logs: emit one JSON object per line when True; otherwise a
            colored console format for local development.
        level: minimum level name ("DEBUG", "INFO", "WARNING", "ERROR",
            "CRITICAL"); unknown names fall back to INFO.
    """
    level_no = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
```

Note: `cache_logger_on_first_use=False` keeps reconfiguration deterministic across tests (and the engine is configured exactly once in production, so the cost is irrelevant).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_logging.py -v`
Expected: PASS — `4 passed`.

- [ ] **Step 5: Lint + type-check the new module**

Run: `ruff check mediascanmonitor/observ/logging.py tests/test_logging.py && mypy mediascanmonitor/observ/logging.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/observ/logging.py tests/test_logging.py
git commit -m "feat(observ): structlog configuration with secret redaction"
```

---

## Task 2: Shared test doubles (`tests/_helpers.py`)

**Files:**
- Create: `tests/_helpers.py`

This is test infrastructure (no `test_` prefix, so pytest does not collect it). It has no test of its own; Tasks 3–5 import from it.

- [ ] **Step 1: Create `tests/_helpers.py`**

```python
"""Shared test doubles and builders for engine/CLI tests (sub-plan 06).

Not collected by pytest (no ``test_`` prefix). Imported as ``tests._helpers``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

import httpx
from mediascanmonitor.config.runtime import FolderRoute, RuntimeConfig, ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult

# FakeWatcher is the single canonical test watcher defined in sub-plan 04
# (`mediascanmonitor/watcher/base.py`); we import it here rather than redefine it.
# Its `emit()`/`current_roots`/`roots_history`/`closed` affordances cover these tests.
from mediascanmonitor.watcher.base import FakeWatcher

__all__ = [
    "FakeClient",
    "FakeWatcher",
    "RecordingAdapter",
    "make_config",
    "make_route",
    "make_server_runtime",
    "wait_for",
]


class FakeClient:
    """Stand-in for httpx.AsyncClient; only ``aclose`` is exercised by the engine."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class RecordingAdapter(ServerAdapter):
    """Adapter that records every ScanRequest it is asked to trigger."""

    server_type = ServerType.plex
    supported_scan_modes = frozenset({ScanMode.targeted, ScanMode.library})

    def __init__(self, server: ServerRuntime, client: httpx.AsyncClient) -> None:
        super().__init__(server, client)
        self.calls: list[ScanRequest] = []

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        self.calls.append(req)
        return TriggerResult(ok=True, status_code=200, detail="ok")

    async def test(self) -> TestResult:
        return TestResult(ok=True, detail="ok")


def make_server_runtime(
    server_id: int,
    *,
    name: str,
    debounce: DebounceMode = DebounceMode.off,
    scan_mode: ScanMode = ScanMode.targeted,
) -> ServerRuntime:
    return ServerRuntime(
        server_id=server_id,
        name=name,
        type=ServerType.plex,
        base_url="http://plex.example:32400",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="PLEX-TOKEN",
        scan_mode=scan_mode,
        debounce_mode=debounce,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )


def make_route(
    server_id: int,
    *,
    name: str,
    path: str,
    library_id: str,
    extensions: Iterable[str] = (),
    scan_mode: ScanMode = ScanMode.targeted,
) -> FolderRoute:
    return FolderRoute(
        server_id=server_id,
        server_name=name,
        path=path,
        extensions=frozenset(extensions),
        library_id=library_id,
        scan_mode=scan_mode,
    )


def make_config(
    routes: list[FolderRoute],
    servers: list[ServerRuntime],
    *,
    ignore_dirs: frozenset[str] = frozenset({"@eaDir", "#snapshot"}),
) -> RuntimeConfig:
    return RuntimeConfig(
        watch_paths=frozenset(r.path for r in routes),
        routes=tuple(routes),
        servers={s.server_id: s for s in servers},
        ignore_dirs=ignore_dirs,
    )


async def wait_for(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    """Poll a predicate by yielding to the event loop until True or timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition not satisfied within timeout")
```

- [ ] **Step 2: Confirm it imports and is not collected**

Run: `python -c "import tests._helpers" && pytest tests/_helpers.py 2>&1 | tail -1`
Expected: import succeeds; pytest reports `no tests ran` (file collected as 0 tests / or "no tests ran in ...").

- [ ] **Step 3: Commit**

```bash
git add tests/_helpers.py
git commit -m "test: shared doubles for engine/CLI tests"
```

---

## Task 3: Engine lifecycle — `__init__`, `start`, `_handle_event`, `aclose`

**Files:**
- Create: `mediascanmonitor/engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing wiring + shutdown tests**

Create `tests/test_engine.py`:

```python
"""Engine wiring, rebuild, atomicity, and shutdown tests (sub-plan 06)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from mediascanmonitor import engine as engine_module
from mediascanmonitor.db.models import DebounceMode, ScanMode
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.pipeline.events import FsEvent, FsEventType

from tests._helpers import (
    FakeClient,
    FakeWatcher,
    RecordingAdapter,
    make_config,
    make_route,
    make_server_runtime,
    wait_for,
)


@pytest.fixture
def stub_repo() -> Repo:
    # build_runtime_config is monkeypatched in every test, so the repo is never queried.
    return cast(Repo, object())


def _patch_factories(
    monkeypatch: pytest.MonkeyPatch, created: dict[int, RecordingAdapter]
) -> None:
    def fake_create_adapter(server: object, client: object) -> RecordingAdapter:
        adapter = RecordingAdapter(
            cast("engine_module.ServerRuntime", server),  # type: ignore[attr-defined]
            cast("engine_module.httpx.AsyncClient", client),  # type: ignore[attr-defined]
        )
        created[adapter.server.server_id] = adapter
        return adapter

    monkeypatch.setattr(engine_module, "create_adapter", fake_create_adapter)
    monkeypatch.setattr(engine_module, "build_client", lambda **_: FakeClient())


async def test_event_routes_to_adapter_trigger(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2", extensions={"mkv"})
    config = make_config([route], [server])
    monkeypatch.setattr(engine_module, "build_runtime_config", lambda repo: config)

    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher)

    # Pre-load one matching event and the end-of-stream sentinel, then run start()
    # to completion deterministically (no sleeps, no signals).
    await watcher.emit(
        FsEvent(path="/data/tv/Shoresy/ep1.mkv", event_type=FsEventType.created, is_dir=False)
    )
    await watcher.aclose()
    await engine.start()
    await engine.aclose()

    assert watcher.roots_history[0] == {"/data/tv"}
    assert set(created) == {1}
    calls = created[1].calls
    assert len(calls) == 1
    req = calls[0]
    assert req.server_id == 1
    assert req.scan_mode is ScanMode.targeted
    assert req.scan_path == "/data/tv/Shoresy"
    assert req.scan_key == "/data/tv/Shoresy"
    assert req.library_id == "2"
    assert req.top_folder == "Shoresy"


async def test_non_matching_extension_is_not_dispatched(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2", extensions={"mkv"})
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher)
    await watcher.emit(
        FsEvent(path="/data/tv/Shoresy/poster.jpg", event_type=FsEventType.created, is_dir=False)
    )
    await watcher.aclose()
    await engine.start()
    await engine.aclose()

    assert created[1].calls == []


async def test_aclose_closes_watcher_debouncer_and_clients(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: dict[int, RecordingAdapter] = {}
    clients: list[FakeClient] = []

    def fake_create_adapter(server: object, client: object) -> RecordingAdapter:
        adapter = RecordingAdapter(
            cast("engine_module.ServerRuntime", server),  # type: ignore[attr-defined]
            cast("engine_module.httpx.AsyncClient", client),  # type: ignore[attr-defined]
        )
        created[adapter.server.server_id] = adapter
        return adapter

    def fake_build_client(**_: object) -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(engine_module, "create_adapter", fake_create_adapter)
    monkeypatch.setattr(engine_module, "build_client", fake_build_client)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher)
    start_task = asyncio.create_task(engine.start())
    await wait_for(lambda: bool(watcher.roots_history))

    await engine.aclose()
    await start_task  # watcher closed -> events() ends -> start() returns

    assert watcher.closed is True
    assert clients and all(c.closed for c in clients)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.engine'` (or `cannot import name 'Engine'`).

- [ ] **Step 3: Implement `engine.py` (lifecycle only; `rebuild` added in Task 4)**

Create `mediascanmonitor/engine.py`:

```python
"""The async Engine: owns the watcher + pipeline and supports live rebuild.

Single event loop, no blocking calls in the loop. The only DB read happens via
``asyncio.to_thread(build_runtime_config, repo)`` at the loop boundary. The
watcher is injectable so non-Linux dev/tests can supply a fake backend.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from mediascanmonitor.config.runtime import (
    RuntimeConfig,
    ServerRuntime,
    build_runtime_config,
)
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.pipeline.debounce import Debouncer
from mediascanmonitor.pipeline.dispatcher import Dispatcher
from mediascanmonitor.pipeline.events import FsEvent, ScanRequest
from mediascanmonitor.pipeline.router import route
from mediascanmonitor.servers.base import ServerAdapter
from mediascanmonitor.servers.http import build_client
from mediascanmonitor.servers.registry import create_adapter
from mediascanmonitor.watcher.base import WatcherBackend

log = structlog.get_logger("engine")


class Engine:
    """Owns one watcher and the routing/debounce/dispatch pipeline."""

    def __init__(self, repo: Repo, *, watcher: WatcherBackend | None = None) -> None:
        self._repo = repo
        self._watcher: WatcherBackend | None = watcher
        self._config: RuntimeConfig | None = None
        self._dispatcher: Dispatcher | None = None
        self._debouncer: Debouncer | None = None
        # Engine-owned, MUTATED IN PLACE on rebuild so the Debouncer (which holds
        # this exact reference) always sees the current per-server policy.
        self._servers: dict[int, ServerRuntime] = {}
        self._clients: dict[int, httpx.AsyncClient] = {}
        self._started = False
        self._lock = asyncio.Lock()

    # -- public lifecycle ----------------------------------------------------

    async def start(self) -> None:
        """Build the runtime, wire the pipeline, then consume watcher events."""
        if self._started:
            raise RuntimeError("Engine.start() called more than once")
        self._started = True

        config = await asyncio.to_thread(build_runtime_config, self._repo)
        self._config = config

        if self._watcher is None:
            # Lazy import keeps the engine importable on non-Linux dev machines.
            from mediascanmonitor.watcher.inotify_backend import InotifyBackend

            self._watcher = InotifyBackend(config.ignore_dirs)

        adapters, clients = self._build_adapters(config)
        self._clients = clients
        self._servers = dict(config.servers)
        self._dispatcher = Dispatcher(adapters)
        self._debouncer = Debouncer(self._dispatch, self._servers)

        self._watcher.set_roots(set(config.watch_paths))
        log.info(
            "engine.started",
            watch_paths=sorted(config.watch_paths),
            servers=len(config.servers),
            routes=len(config.routes),
        )

        async for event in self._watcher.events():
            await self._handle_event(event)

    async def aclose(self) -> None:
        """Stop the event stream and release the watcher, debouncer, and clients."""
        if self._watcher is not None:
            await self._watcher.aclose()
        if self._debouncer is not None:
            await self._debouncer.aclose()
        for client in self._clients.values():
            await client.aclose()
        self._clients = {}
        log.info("engine.closed")

    # -- internals -----------------------------------------------------------

    def _build_adapters(
        self, config: RuntimeConfig
    ) -> tuple[dict[int, ServerAdapter], dict[int, httpx.AsyncClient]]:
        adapters: dict[int, ServerAdapter] = {}
        clients: dict[int, httpx.AsyncClient] = {}
        for server_id, server in config.servers.items():
            client = build_client(
                verify_tls=server.verify_tls, timeout_seconds=server.timeout_seconds
            )
            clients[server_id] = client
            adapters[server_id] = create_adapter(server, client)
        return adapters, clients

    async def _dispatch(self, req: ScanRequest) -> None:
        """Adapter for the Debouncer's ``Awaitable[None]`` dispatch signature."""
        dispatcher = self._dispatcher
        if dispatcher is None:
            return
        await dispatcher.dispatch(req)

    async def _handle_event(self, event: FsEvent) -> None:
        # Snapshot the immutable config locally BEFORE any await so a concurrent
        # rebuild swap never splits a single event across two configurations.
        config = self._config
        debouncer = self._debouncer
        if config is None or debouncer is None:
            return
        for req in route(event, config):
            await debouncer.submit(req)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_engine.py -v`
Expected: PASS — `3 passed`.

- [ ] **Step 5: Type-check + lint**

Run: `mypy mediascanmonitor/engine.py && ruff check mediascanmonitor/engine.py tests/test_engine.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/engine.py tests/test_engine.py
git commit -m "feat(engine): async lifecycle wiring watcher->route->debounce->dispatch"
```

---

## Task 4: Engine `rebuild()` — atomic, no restart, no dropped events

**Files:**
- Modify: `mediascanmonitor/engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing rebuild tests**

Append to `tests/test_engine.py`:

```python
async def test_rebuild_adds_then_removes_roots_and_reroutes(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    tv = make_route(1, name="plex", path="/data/tv", library_id="2")
    movies = make_route(1, name="plex", path="/data/movies", library_id="1")

    holder = SimpleNamespace(current=make_config([tv], [server]))
    monkeypatch.setattr(engine_module, "build_runtime_config", lambda repo: holder.current)

    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher)
    start_task = asyncio.create_task(engine.start())
    await wait_for(lambda: bool(watcher.roots_history))
    assert watcher.current_roots == {"/data/tv"}

    # --- add a second folder, rebuild -> union grows ---
    holder.current = make_config([tv, movies], [server])
    await engine.rebuild()
    assert watcher.current_roots == {"/data/tv", "/data/movies"}

    await watcher.emit(
        FsEvent(path="/data/movies/Dune/d.mkv", event_type=FsEventType.created, is_dir=False)
    )
    await wait_for(lambda: any(c.scan_path == "/data/movies/Dune" for c in created[1].calls))

    # --- remove the original folder, rebuild -> root drops, no more routing there ---
    holder.current = make_config([movies], [server])
    await engine.rebuild()
    assert watcher.current_roots == {"/data/movies"}

    before = len(created[1].calls)
    await watcher.emit(
        FsEvent(path="/data/tv/Shoresy/ep1.mkv", event_type=FsEventType.created, is_dir=False)
    )
    # Give the loop ample chances; the removed root must produce NO new dispatch.
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(created[1].calls) == before

    await engine.aclose()
    await start_task


async def test_rebuild_closes_old_clients_and_swaps_adapters(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: list[RecordingAdapter] = []
    clients: list[FakeClient] = []

    def fake_create_adapter(server: object, client: object) -> RecordingAdapter:
        adapter = RecordingAdapter(
            cast("engine_module.ServerRuntime", server),  # type: ignore[attr-defined]
            cast("engine_module.httpx.AsyncClient", client),  # type: ignore[attr-defined]
        )
        created.append(adapter)
        return adapter

    def fake_build_client(**_: object) -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(engine_module, "create_adapter", fake_create_adapter)
    monkeypatch.setattr(engine_module, "build_client", fake_build_client)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    tv = make_route(1, name="plex", path="/data/tv", library_id="2")
    holder = SimpleNamespace(current=make_config([tv], [server]))
    monkeypatch.setattr(engine_module, "build_runtime_config", lambda repo: holder.current)

    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher)
    start_task = asyncio.create_task(engine.start())
    await wait_for(lambda: bool(watcher.roots_history))

    assert len(clients) == 1  # one client built at start
    await engine.rebuild()
    assert len(clients) == 2  # a fresh client built on rebuild
    assert clients[0].closed is True  # the OLD client was closed after the swap
    assert clients[1].closed is False
    assert len(created) == 2  # adapters rebuilt

    await engine.aclose()
    await start_task
    assert clients[1].closed is True


async def test_rebuild_before_start_raises(stub_repo: Repo) -> None:
    engine = Engine(stub_repo, watcher=FakeWatcher())
    with pytest.raises(RuntimeError, match="before start"):
        await engine.rebuild()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_engine.py -k rebuild -v`
Expected: FAIL — `AttributeError: 'Engine' object has no attribute 'rebuild'`.

- [ ] **Step 3: Implement `rebuild()`**

In `mediascanmonitor/engine.py`, add this method to `Engine` (place it directly after `aclose`, before `_build_adapters`):

```python
    async def rebuild(self) -> None:
        """Rebuild the runtime snapshot and atomically swap it in — no restart.

        The DB read (``to_thread``) and old-client teardown (``aclose``) bracket a
        fully synchronous swap block, so no awaitable point splits a routing
        decision: every in-flight event uses one consistent snapshot.
        """
        if not self._started or self._dispatcher is None or self._watcher is None:
            raise RuntimeError("Engine.rebuild() called before start()")

        async with self._lock:
            new_config = await asyncio.to_thread(build_runtime_config, self._repo)
            new_adapters, new_clients = self._build_adapters(new_config)

            old_clients = self._clients
            old_paths = self._config.watch_paths if self._config else frozenset()
            new_paths = new_config.watch_paths

            # --- atomic swap (NO await between these statements) -------------
            self._dispatcher.set_adapters(new_adapters)
            self._servers.clear()
            self._servers.update(new_config.servers)
            self._config = new_config
            self._clients = new_clients
            self._watcher.set_roots(set(new_paths))
            # ----------------------------------------------------------------

            log.info(
                "engine.rebuilt",
                added=sorted(new_paths - old_paths),
                removed=sorted(old_paths - new_paths),
                servers=len(new_config.servers),
                routes=len(new_config.routes),
            )

            # Safe to await now: new requests already use new adapters/clients.
            for client in old_clients.values():
                await client.aclose()
```

- [ ] **Step 4: Run the rebuild tests to verify they pass**

Run: `pytest tests/test_engine.py -k rebuild -v`
Expected: PASS — `3 passed`.

- [ ] **Step 5: Run the full engine suite + type-check + lint**

Run: `pytest tests/test_engine.py -v && mypy mediascanmonitor/engine.py && ruff check mediascanmonitor/engine.py tests/test_engine.py`
Expected: `6 passed`; no mypy/ruff errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/engine.py tests/test_engine.py
git commit -m "feat(engine): atomic rebuild() with watch-set diff and client teardown"
```

---

## Task 5: CLI — implement `run --no-web`

**Files:**
- Modify: `mediascanmonitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Revise the existing stub test and add new CLI tests**

> **Existing test revised:** `test_run_command_not_yet_implemented` asserted `main(["run"])` raises `SystemExit` containing `"Phase 1"`. That stub is gone — `run` (no flag) now prints a Phase-3 message to stderr and returns exit code `2` (no exception). Replace that test with `test_run_without_no_web_prints_phase3_message` below. The other four existing tests (`test_version_string_is_set`, `test_no_command_prints_help_and_succeeds`, `test_version_flag_exits_zero`, `test_parser_exposes_no_web_flag`) are unchanged and must stay green.

Replace the entire contents of `tests/test_cli.py` with:

```python
"""CLI tests: parser smoke (Phase 0) + headless run wiring (Phase 1, sub-plan 06)."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from mediascanmonitor import __version__
from mediascanmonitor import cli as cli_module
from mediascanmonitor.cli import build_parser, main, serve_headless
from mediascanmonitor.db.repo import Repo

from tests._helpers import FakeClient, FakeWatcher, RecordingAdapter, make_config


# --- Phase 0 parser smoke (unchanged) --------------------------------------


def test_version_string_is_set() -> None:
    assert __version__
    assert __version__.count(".") >= 2


def test_no_command_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "media-scan-monitor" in out
    assert "run" in out


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parser_exposes_no_web_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--no-web"])
    assert args.command == "run"
    assert args.no_web is True


# --- Phase 1: `run` dispatch (revised + new) -------------------------------


def test_run_without_no_web_prints_phase3_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Revised from the Phase-0 stub: clear message to stderr, exit code 2, no traceback.
    code = main(["run"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Phase 3" in err
    assert "--no-web" in err


def test_run_no_web_invokes_serve_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(cli_module, "_build_repo", lambda: cast(Repo, object()))
    monkeypatch.setattr(cli_module, "configure_logging", lambda **_: None)

    async def fake_serve(repo: Repo) -> None:
        calls.append(True)

    monkeypatch.setattr(cli_module, "serve_headless", fake_serve)

    assert main(["run", "--no-web"]) == 0
    assert calls == [True]


def test_run_no_web_reports_startup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> Repo:
        raise RuntimeError("no /config/secret.key")

    monkeypatch.setattr(cli_module, "_build_repo", boom)
    monkeypatch.setattr(cli_module, "configure_logging", lambda **_: None)

    code = main(["run", "--no-web"])
    assert code == 1
    assert "startup error" in capsys.readouterr().err


# --- serve_headless coroutine (testable assembly, no real signals) ---------


async def test_serve_headless_shuts_down_on_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module.engine_module,
        "build_runtime_config",
        lambda repo: make_config([], []),
    )
    monkeypatch.setattr(
        cli_module.engine_module,
        "create_adapter",
        lambda server, client: RecordingAdapter(server, client),
    )
    monkeypatch.setattr(cli_module.engine_module, "build_client", lambda **_: FakeClient())

    watcher = FakeWatcher()
    stop = asyncio.Event()
    stop.set()  # request shutdown immediately

    await serve_headless(
        cast(Repo, object()), watcher=watcher, stop_event=stop, install_signals=False
    )

    assert watcher.closed is True  # engine.aclose() ran -> clean shutdown
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'serve_headless'` (and the run-dispatch tests fail).

- [ ] **Step 3: Implement the CLI changes**

Replace the entire contents of `mediascanmonitor/cli.py` with:

```python
"""Command-line entrypoint.

Phase 1 implements ``run --no-web`` (headless engine). The full web dashboard
arrives in Phase 3; ``run`` without ``--no-web`` prints a clear message and
exits non-zero rather than crashing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from mediascanmonitor import __version__
from mediascanmonitor import engine as engine_module
from mediascanmonitor.db.crypto import SecretBox, load_or_create_key
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.session import init_db
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.logging import configure_logging
from mediascanmonitor.watcher.base import WatcherBackend

__all__ = ["build_parser", "main", "serve_headless"]

_DEFAULT_DB_PATH = "/config/app.db"
_DEFAULT_KEY_PATH = "/config/secret.key"


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="media-scan-monitor",
        description=(
            "Watch media folders and fan out targeted scan/refresh events to "
            "Plex, Emby, Jellyfin, Audiobookshelf, and generic webhooks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    run = subparsers.add_parser(
        "run", help="Run the watcher engine (and the web dashboard unless --no-web)."
    )
    run.add_argument(
        "--no-web",
        action="store_true",
        help="Run the engine headless, without serving the web dashboard.",
    )
    return parser


def _build_repo() -> Repo:
    """Assemble the repository from env/Docker config. Raises on misconfiguration."""
    key_path = Path(os.environ.get("MSM_SECRET_KEY_FILE", _DEFAULT_KEY_PATH))
    db_path = Path(os.environ.get("MSM_DB_PATH", _DEFAULT_DB_PATH))
    env_key = os.environ.get("MSM_SECRET_KEY")
    box = SecretBox(load_or_create_key(key_path, env_key=env_key))
    session_factory = init_db(db_path)
    return Repo(session_factory, box)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # e.g. non-main thread / Windows
            loop.add_signal_handler(sig, stop.set)


async def serve_headless(
    repo: Repo,
    *,
    watcher: WatcherBackend | None = None,
    stop_event: asyncio.Event | None = None,
    install_signals: bool = True,
) -> None:
    """Run the engine until SIGINT/SIGTERM (or ``stop_event``), then shut down.

    Designed for testability: inject ``watcher`` and ``stop_event`` and set
    ``install_signals=False`` to drive the lifecycle without real signals.
    """
    engine = Engine(repo, watcher=watcher)
    stop = stop_event if stop_event is not None else asyncio.Event()

    if install_signals:
        _install_signal_handlers(asyncio.get_running_loop(), stop)

    start_task = asyncio.create_task(engine.start())
    stop_task = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({start_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await engine.aclose()  # closes the watcher -> events() ends -> start_task returns
        with contextlib.suppress(asyncio.CancelledError):
            await start_task
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.no_web:
        print(
            "The web dashboard arrives in Phase 3. Re-run with `--no-web` to start "
            "the headless engine.",
            file=sys.stderr,
        )
        return 2

    try:
        repo = _build_repo()
    except Exception as exc:  # noqa: BLE001 — fail fast with a clear message, not a traceback
        print(f"startup error: {exc}", file=sys.stderr)
        return 1

    configure_logging()
    asyncio.run(serve_headless(repo))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "run":
        return _cmd_run(args)

    parser.error(f"unknown command: {args.command!r}")
    return 2  # unreachable; parser.error exits, but keeps mypy/control-flow honest


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `_cmd_run` references the module-global `serve_headless` and `configure_logging`, so the tests can monkeypatch `cli_module.serve_headless` / `cli_module.configure_logging` and have `_cmd_run` pick up the patched versions. `engine_module` is imported so tests can patch the engine's factories through the CLI module too.

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS — `8 passed`.

- [ ] **Step 5: Type-check + lint the CLI**

Run: `mypy mediascanmonitor/cli.py && ruff check mediascanmonitor/cli.py tests/test_cli.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/cli.py tests/test_cli.py
git commit -m "feat(cli): implement headless run --no-web; Phase-3 message for web mode"
```

---

## Task 6: Full-suite verification gate

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest`
Expected: all tests pass (logging 4 + engine 6 + cli 8 + any sibling sub-plan suites), e.g. `... passed`.

- [ ] **Step 2: Type-check the package**

Run: `mypy mediascanmonitor`
Expected: `Success: no issues found`.

- [ ] **Step 3: Lint + format check**

Run: `ruff check . && ruff format --check .`
Expected: no errors; "would reformat" count is 0.

- [ ] **Step 4: Final commit (only if Steps 1–3 surfaced fixes)**

```bash
git add -A
git commit -m "chore(phase1-06): green ruff/mypy/pytest for engine, cli, logging"
```

---

## Self-Review

**1. Spec coverage**

| Spec requirement (from the prompt / contract §10) | Task |
|---|---|
| `observ/logging.py` `configure_logging(json_logs, level)` + secret-not-emitted smoke | Task 1 |
| `Engine.__init__(repo, *, watcher=None)`, injectable watcher (default InotifyBackend) | Task 3 (`__init__`, lazy `InotifyBackend`) |
| `Engine.start()` — build runtime → adapters/clients → dispatcher+debouncer → set roots → consume events → route → submit | Task 3 |
| DB calls via `asyncio.to_thread` | Task 3 (`start`), Task 4 (`rebuild`) |
| `Engine.rebuild()` — rebuild config, diff watch_paths + `set_roots`, rebuild adapters, `set_adapters`, swap snapshot, no restart, no dropped events | Task 4 |
| `Engine.aclose()` | Task 3 |
| Engine wiring test (FakeWatcher + recording fake adapter via monkeypatched `create_adapter`, `off` debounce) → assert `trigger` got the right `ScanRequest` | Task 3 `test_event_routes_to_adapter_trigger` |
| rebuild test: add folder → union to `set_roots`; new folder routes; removed folder no longer routes; no event dropped (atomic swap) | Task 4 `test_rebuild_adds_then_removes_roots_and_reroutes` |
| CLI `run --no-web` starts + shuts down cleanly on simulated signal (testable coroutine with injected stop/watcher) | Task 5 `test_serve_headless_shuts_down_on_stop_event`, `test_run_no_web_invokes_serve_headless` |
| `run` (no flag) prints Phase-3 message, exits non-zero (code 2) — not a traceback | Task 5 `test_run_without_no_web_prints_phase3_message` |
| Revise existing `test_run_command_not_yet_implemented` (note explicitly) | Task 5 Step 1 note + replacement |
| logging smoke test | Task 1 |
| Fail fast on startup/config errors with a clear message | Task 5 `_cmd_run` try/except + `test_run_no_web_reports_startup_failure` |
| mypy --strict / ruff / line-length 100 / `from __future__ import annotations` | Every code block; Task 6 gate |

No gaps. No contract deviations: all consumed names match §1–§10 verbatim; rebuild follows §10's listed steps; the debouncer-keeps-live-servers-ref and one-client-per-server choices fill silences the contract leaves open (documented under "Design decisions").

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to Task N". Every code step shows complete code; every command shows expected output.

**3. Type consistency:** `serve_headless(repo, *, watcher, stop_event, install_signals)`, `_build_repo() -> Repo`, `_cmd_run(args) -> int`, `Engine.__init__/start/rebuild/aclose/_dispatch/_handle_event/_build_adapters`, `configure_logging(*, json_logs, level)`, `_redact_secrets(logger, method_name, event_dict)` are referenced identically across tasks. `RecordingAdapter`/`FakeClient`/`make_config`/`make_route`/`make_server_runtime`/`wait_for` defined once in `tests/_helpers.py` (which re-exports the canonical `FakeWatcher` from `mediascanmonitor.watcher.base`, sub-plan 04) and imported with matching names/signatures in Tasks 3–5. `Debouncer(self._dispatch, self._servers)` matches the `Callable[[ScanRequest], Awaitable[None]]` signature via the `_dispatch` wrapper. `cli_module.engine_module` is exposed because `cli.py` does `from mediascanmonitor import engine as engine_module`, which the CLI test monkeypatches.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-phase1-06-engine-cli.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
