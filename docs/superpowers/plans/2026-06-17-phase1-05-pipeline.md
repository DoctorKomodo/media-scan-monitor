# Phase 1 — Sub-plan 05: Pipeline (filters / router / debounce / dispatcher) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the event-processing pipeline that turns a raw `FsEvent` into zero-or-more
per-server `ScanRequest`s, debounces bursts per server, and dispatches each request to the
matching `ServerAdapter` with full failure isolation.

**Architecture:** Four small, single-responsibility modules under `mediascanmonitor/pipeline/`:
`filters.py` (pure predicate functions), `router.py` (pure `FsEvent` → `list[ScanRequest]`),
`debounce.py` (async `Debouncer` with an injectable clock), and `dispatcher.py` (async
`Dispatcher` that isolates adapter exceptions). Everything is `asyncio`-native; the only
non-stdlib touch is the `structlog` logger already in the dependency set. The debouncer uses an
injectable `sleep` callable so tests drive it with a deterministic fake clock — **no real
sleeps in tests**.

**Tech Stack:** Python 3.14+, stdlib `asyncio`, `structlog` (logging only), `pytest==9.1.0` +
`pytest-asyncio==1.4.0` (auto mode). `ruff` lint+format (line length 100), `mypy --strict`.

---

## Prerequisites (read before starting)

This sub-plan **consumes** types frozen in
`docs/superpowers/plans/2026-06-17-phase1-00-interface-contract.md`. Per the forward-only
dependency order, sub-plans **02** (`pipeline/events.py`, `config/runtime.py`,
`config/defaults.py`) and **03** (`servers/base.py`) are implemented and merged **before** this
sub-plan, so the following imports already resolve:

- `mediascanmonitor.db.models`: `ServerType`, `ScanMode`, `DebounceMode` (enums, §1).
- `mediascanmonitor.pipeline.events`: `FsEvent`, `FsEventType`, `ScanRequest` (§5).
- `mediascanmonitor.config.runtime`: `ServerRuntime`, `FolderRoute`, `RuntimeConfig` (§6).
- `mediascanmonitor.servers.base`: `ServerAdapter`, `TriggerResult`, `TestResult` (§7).

This plan **does not redefine or rename** any of these. It implements only §9 (pipeline).

**Cross-plan invariants this plan must honor (verbatim from the contract):**

1. **Empty extension set means "all extensions"** — `extension_matches` returns `True` for an
   empty `extensions` set.
2. **`scan_key`** = `scan_path` for `targeted`, `f"lib:{library_id}"` for `library`. Set in
   `route()`, consumed by `Debouncer`.
3. **Secrets** never appear in logs/URLs/`__repr__`. (This plan logs only ids/paths/names.)
4. **Paths** are already normalized (absolute, no trailing slash except root) by the time they
   reach the pipeline; the router compares normalized paths and emits normalized scan paths.
5. **Prefix match** in `route()` is a path-**segment** prefix (`/a/b` matches `/a/b/c` but not
   `/a/bc`) — implemented with a separator-aware check, not raw `str.startswith`.
6. **Failure isolation:** a single adapter/server error becomes `TriggerResult(ok=False, ...)`
   and is logged; it never propagates out of `Dispatcher.dispatch`.

**Chosen trailing-debounce semantics (documented here, pinned by tests in Task 5):**
`trailing` is a **classic trailing-edge / reset-on-each-event** debounce. Every `submit` for an
already-pending `(server_id, scan_key)` **cancels the pending timer and arms a fresh
`window`-second timer**. The dispatch fires exactly once, `window` seconds after the **most
recent** matching event, carrying the **most recent** `ScanRequest`. This matches PLAN.md
("collapse a burst … into a single trigger **after the folder settles**") and the contract's
"(re)arm timer for window seconds". `off` bypasses the debouncer entirely (await dispatch
immediately). `aclose()` **cancels and drops** all pending timers (deterministic; no flush).
`update_servers()` (called by `Engine.rebuild`, contract §9/§10) swaps the per-server policy map
in place, cancelling pending timers for servers that were removed and leaving survivors armed.

**File structure produced by this plan:**

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/pipeline/filters.py` | `is_ignored`, `extension_matches` (pure predicates) |
| `mediascanmonitor/pipeline/router.py` | `compute_scan_path`, `route` (pure event → requests) |
| `mediascanmonitor/pipeline/debounce.py` | `Debouncer` (async, injectable clock) |
| `mediascanmonitor/pipeline/dispatcher.py` | `Dispatcher` (async, exception isolation) |
| `tests/pipeline/__init__.py` | makes `tests.pipeline` importable |
| `tests/pipeline/factories.py` | shared builders for runtime/route/config types |
| `tests/pipeline/clock.py` | `ManualClock` fake-clock helper |
| `tests/pipeline/test_filters.py` | filters unit tests |
| `tests/pipeline/test_router.py` | router unit tests |
| `tests/pipeline/test_debounce.py` | debounce unit tests (fake clock) |
| `tests/pipeline/test_dispatcher.py` | dispatcher unit tests |

> **Note on commands:** run from the repo root. `pytest` is configured with `asyncio_mode =
> auto` (see `pyproject.toml`), so `async def test_*` functions need no decorator. mypy is the
> authority for the package (`mypy mediascanmonitor`); test-helper modules are not type-checked
> under `--strict` but must stay `ruff`-clean.

---

## Task 1: Test package scaffolding + shared factories

This task creates the importable test package and the DRY factory helpers used by Tasks 3, 4,
and 5. No production code yet.

**Files:**
- Create: `tests/pipeline/__init__.py`
- Create: `tests/pipeline/factories.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/pipeline/__init__.py` with exactly this content (a single blank line is fine; the
file just needs to exist so `tests.pipeline.*` is importable):

```python
```

- [ ] **Step 2: Create the shared factories**

These builders return fully-populated contract types so individual tests stay short. Field
names and types match contract §6 exactly — do not rename.

Create `tests/pipeline/factories.py`:

```python
from __future__ import annotations

from collections.abc import Iterable

from mediascanmonitor.config.runtime import FolderRoute, RuntimeConfig, ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType


def make_server_runtime(
    *,
    server_id: int = 1,
    name: str = "plex-1",
    server_type: ServerType = ServerType.plex,
    scan_mode: ScanMode = ScanMode.targeted,
    debounce_mode: DebounceMode = DebounceMode.trailing,
    debounce_window_seconds: int = 30,
) -> ServerRuntime:
    return ServerRuntime(
        server_id=server_id,
        name=name,
        type=server_type,
        base_url="http://plex:32400",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="token",
        scan_mode=scan_mode,
        debounce_mode=debounce_mode,
        debounce_window_seconds=debounce_window_seconds,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )


def make_folder_route(
    *,
    server_id: int = 1,
    server_name: str = "plex-1",
    path: str = "/data/tv",
    extensions: frozenset[str] = frozenset({"mkv"}),
    library_id: str | None = "2",
    scan_mode: ScanMode = ScanMode.targeted,
) -> FolderRoute:
    return FolderRoute(
        server_id=server_id,
        server_name=server_name,
        path=path,
        extensions=extensions,
        library_id=library_id,
        scan_mode=scan_mode,
    )


def make_runtime_config(
    routes: Iterable[FolderRoute],
    *,
    servers: dict[int, ServerRuntime] | None = None,
    ignore_dirs: frozenset[str] = frozenset({"@eaDir", "#snapshot"}),
) -> RuntimeConfig:
    routes_tuple = tuple(routes)
    return RuntimeConfig(
        watch_paths=frozenset(r.path for r in routes_tuple),
        routes=routes_tuple,
        servers=servers if servers is not None else {},
        ignore_dirs=ignore_dirs,
    )
```

- [ ] **Step 3: Verify the factories import cleanly**

Run: `python -c "import tests.pipeline.factories"`
Expected: no output, exit code 0 (confirms the contract types from sub-plans 02/03 resolve).

- [ ] **Step 4: Lint the new files**

Run: `ruff check tests/pipeline/ && ruff format --check tests/pipeline/`
Expected: `All checks passed!` and no formatting diff.

- [ ] **Step 5: Commit**

```bash
git add tests/pipeline/__init__.py tests/pipeline/factories.py
git commit -m "test(pipeline): add test package scaffolding and shared factories"
```

---

## Task 2: `filters.py` — ignore-dir and extension predicates

**Files:**
- Create: `mediascanmonitor/pipeline/filters.py`
- Test: `tests/pipeline/test_filters.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_filters.py`:

```python
from __future__ import annotations

from mediascanmonitor.pipeline.filters import extension_matches, is_ignored

IGNORE = frozenset({"@eaDir", "#snapshot"})


def test_is_ignored_matches_ignore_dir_as_path_segment() -> None:
    assert is_ignored("/data/tv/@eaDir/thumb.jpg", IGNORE) is True


def test_is_ignored_matches_when_ignore_dir_is_final_segment() -> None:
    assert is_ignored("/data/tv/show/#snapshot", IGNORE) is True


def test_is_ignored_does_not_match_substring_of_a_segment() -> None:
    # "@eaDir" is a substring of "foo@eaDir" but NOT a whole path segment.
    assert is_ignored("/data/tv/foo@eaDir/file.mkv", IGNORE) is False


def test_is_ignored_clean_path_is_not_ignored() -> None:
    assert is_ignored("/data/tv/Shoresy/S01/ep.mkv", IGNORE) is False


def test_is_ignored_empty_ignore_set_never_ignores() -> None:
    assert is_ignored("/data/tv/@eaDir/x.mkv", frozenset()) is False


def test_extension_matches_empty_set_means_all() -> None:
    # Invariant 1: empty set => match every file.
    assert extension_matches("/data/tv/show/ep.mkv", frozenset()) is True
    assert extension_matches("/data/tv/show/notes.txt", frozenset()) is True


def test_extension_matches_hit() -> None:
    assert extension_matches("/data/tv/show/ep.mkv", frozenset({"mkv", "mp4"})) is True


def test_extension_matches_miss() -> None:
    assert extension_matches("/data/tv/show/ep.avi", frozenset({"mkv", "mp4"})) is False


def test_extension_matches_is_case_insensitive() -> None:
    # FolderRoute.extensions are normalized lowercase; the file on disk may be uppercase.
    assert extension_matches("/data/tv/show/EP.MKV", frozenset({"mkv"})) is True


def test_extension_matches_no_extension_is_a_miss() -> None:
    assert extension_matches("/data/tv/show/README", frozenset({"mkv"})) is False


def test_extension_matches_dotfile_has_no_extension() -> None:
    # ".hidden" is a dotfile, not a "hidden"-extension file.
    assert extension_matches("/data/tv/show/.hidden", frozenset({"hidden"})) is False


def test_extension_matches_ignores_dots_in_directory_names() -> None:
    # The dot is in a directory name; the basename "movie" has no extension.
    assert extension_matches("/data/tv/v1.2/movie", frozenset({"2"})) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/pipeline/test_filters.py -v`
Expected: collection error / `ModuleNotFoundError: No module named
'mediascanmonitor.pipeline.filters'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/pipeline/filters.py`:

```python
from __future__ import annotations


def is_ignored(path: str, ignore_dirs: frozenset[str]) -> bool:
    """Return True if any *path segment* of ``path`` is in ``ignore_dirs``.

    Segment-aware (not substring): ``/a/@eaDir/b`` is ignored, but ``/a/foo@eaDir`` is not.
    """
    if not ignore_dirs:
        return False
    return any(segment in ignore_dirs for segment in path.split("/"))


def extension_matches(path: str, extensions: frozenset[str]) -> bool:
    """Return True if ``path``'s file extension is in ``extensions``.

    An empty ``extensions`` set means "match all extensions" (invariant 1). ``extensions`` are
    assumed normalized (lowercase, no leading dot); the comparison lowercases the file's
    extension so on-disk casing does not matter.
    """
    if not extensions:
        return True
    name = path.rsplit("/", 1)[-1]
    base, dot, ext = name.rpartition(".")
    if not dot or not base:
        # No dot at all, or a dotfile like ".hidden" (empty base) -> no real extension.
        return False
    return ext.lower() in extensions
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/pipeline/test_filters.py -v`
Expected: `12 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `ruff check mediascanmonitor/pipeline/filters.py tests/pipeline/test_filters.py && ruff format --check mediascanmonitor/pipeline/filters.py tests/pipeline/test_filters.py && mypy mediascanmonitor/pipeline/filters.py`
Expected: `All checks passed!`, no format diff, and `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/pipeline/filters.py tests/pipeline/test_filters.py
git commit -m "feat(pipeline): add ignore-dir and extension filter predicates"
```

---

## Task 3: `router.py` — `compute_scan_path` + `route`

This task is split into two sub-steps with their own tests because `compute_scan_path` is a
pure helper that `route` builds on. Write both test groups first (one file), implement
`compute_scan_path`, then implement `route`.

**Files:**
- Create: `mediascanmonitor/pipeline/router.py`
- Test: `tests/pipeline/test_router.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_router.py`:

```python
from __future__ import annotations

from mediascanmonitor.db.models import ScanMode
from mediascanmonitor.pipeline.events import FsEvent, FsEventType
from mediascanmonitor.pipeline.router import compute_scan_path, route
from tests.pipeline.factories import make_folder_route, make_runtime_config


def _event(path: str) -> FsEvent:
    return FsEvent(path=path, event_type=FsEventType.created, is_dir=False)


# --- compute_scan_path -------------------------------------------------------

def test_compute_scan_path_file_two_levels_deep() -> None:
    scan_path, top = compute_scan_path("/data/tv", "/data/tv/Shoresy/S01/ep.mkv")
    assert scan_path == "/data/tv/Shoresy"
    assert top == "Shoresy"


def test_compute_scan_path_file_one_level_deep() -> None:
    scan_path, top = compute_scan_path("/data/tv", "/data/tv/Shoresy/ep.mkv")
    assert scan_path == "/data/tv/Shoresy"
    assert top == "Shoresy"


def test_compute_scan_path_file_directly_in_root() -> None:
    # File sits directly in folder_root: top_folder is None, scan_path == folder_root.
    scan_path, top = compute_scan_path("/data/tv", "/data/tv/loose.mkv")
    assert scan_path == "/data/tv"
    assert top is None


def test_compute_scan_path_handles_root_folder_without_double_slash() -> None:
    scan_path, top = compute_scan_path("/", "/Shoresy/ep.mkv")
    assert scan_path == "/Shoresy"
    assert top == "Shoresy"


# --- route: prefix correctness ----------------------------------------------

def test_route_segment_prefix_matches_child_path() -> None:
    config = make_runtime_config([make_folder_route(path="/data/tv")])
    reqs = route(_event("/data/tv/Shoresy/ep.mkv"), config)
    assert len(reqs) == 1
    assert reqs[0].server_id == 1


def test_route_segment_prefix_rejects_sibling_with_shared_prefix() -> None:
    # Route "/data/tv" must NOT match "/data/tvshows/..." (invariant 5).
    config = make_runtime_config([make_folder_route(path="/data/tv")])
    reqs = route(_event("/data/tvshows/ep.mkv"), config)
    assert reqs == []


def test_route_matches_file_exactly_at_root_is_handled() -> None:
    config = make_runtime_config([make_folder_route(path="/data/tv")])
    reqs = route(_event("/data/tv/loose.mkv"), config)
    assert len(reqs) == 1
    assert reqs[0].scan_path == "/data/tv"
    assert reqs[0].top_folder is None


# --- route: ignore dirs ------------------------------------------------------

def test_route_skips_ignored_dirs() -> None:
    config = make_runtime_config([make_folder_route(path="/data/tv")])
    reqs = route(_event("/data/tv/@eaDir/thumb.mkv"), config)
    assert reqs == []


# --- route: fan-out to matching subscribers only ----------------------------

def test_route_fans_out_only_to_extension_matching_subscribers() -> None:
    # Two servers subscribe to the SAME folder path with DIFFERENT extension sets.
    route_mkv = make_folder_route(server_id=1, server_name="plex-mkv", extensions=frozenset({"mkv"}))
    route_srt = make_folder_route(server_id=2, server_name="plex-srt", extensions=frozenset({"srt"}))
    config = make_runtime_config([route_mkv, route_srt])

    mkv_reqs = route(_event("/data/tv/Shoresy/ep.mkv"), config)
    assert {r.server_id for r in mkv_reqs} == {1}

    srt_reqs = route(_event("/data/tv/Shoresy/ep.srt"), config)
    assert {r.server_id for r in srt_reqs} == {2}


def test_route_empty_extension_set_subscriber_matches_any_file() -> None:
    route_all = make_folder_route(server_id=3, server_name="webhook", extensions=frozenset())
    config = make_runtime_config([route_all])
    reqs = route(_event("/data/tv/Shoresy/ep.flac"), config)
    assert {r.server_id for r in reqs} == {3}


# --- route: scan_mode / scan_key --------------------------------------------

def test_route_targeted_sets_scan_path_and_scan_key() -> None:
    config = make_runtime_config(
        [make_folder_route(scan_mode=ScanMode.targeted, library_id="2")]
    )
    req = route(_event("/data/tv/Shoresy/ep.mkv"), config)[0]
    assert req.scan_mode is ScanMode.targeted
    assert req.scan_path == "/data/tv/Shoresy"
    assert req.scan_key == "/data/tv/Shoresy"  # invariant 2: scan_key == scan_path
    assert req.library_id == "2"
    assert req.top_folder == "Shoresy"


def test_route_library_mode_sets_null_scan_path_and_lib_scan_key() -> None:
    config = make_runtime_config(
        [make_folder_route(scan_mode=ScanMode.library, library_id="7")]
    )
    req = route(_event("/data/tv/Shoresy/ep.mkv"), config)[0]
    assert req.scan_mode is ScanMode.library
    assert req.scan_path is None          # library-mode servers get scan_path=None
    assert req.top_folder is None
    assert req.scan_key == "lib:7"        # invariant 2: f"lib:{library_id}"
    assert req.library_id == "7"


def test_route_carries_event_context() -> None:
    config = make_runtime_config([make_folder_route()])
    req = route(_event("/data/tv/Shoresy/ep.mkv"), config)[0]
    assert req.event_type is FsEventType.created
    assert req.file_path == "/data/tv/Shoresy/ep.mkv"
    assert req.server_name == "plex-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/pipeline/test_router.py -v`
Expected: collection error / `ModuleNotFoundError: No module named
'mediascanmonitor.pipeline.router'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/pipeline/router.py`:

```python
from __future__ import annotations

from mediascanmonitor.config.runtime import RuntimeConfig
from mediascanmonitor.db.models import ScanMode
from mediascanmonitor.pipeline.events import FsEvent, ScanRequest
from mediascanmonitor.pipeline.filters import extension_matches, is_ignored


def compute_scan_path(folder_root: str, file_path: str) -> tuple[str, str | None]:
    """Return ``(scan_path, top_folder)`` for a file under ``folder_root``.

    ``scan_path`` is ``folder_root`` joined with the first path segment of ``file_path`` below
    it (the proven Plex show/movie-folder behavior). If the file sits directly in
    ``folder_root`` (no intermediate folder), ``top_folder`` is ``None`` and ``scan_path`` ==
    ``folder_root``. Callers guarantee ``file_path`` is below ``folder_root``.
    """
    relative = file_path[len(folder_root):].lstrip("/")
    parts = relative.split("/")
    if len(parts) >= 2:
        top_folder = parts[0]
        return f"{folder_root.rstrip('/')}/{top_folder}", top_folder
    return folder_root, None


def _is_path_prefix(prefix: str, path: str) -> bool:
    """Segment-aware prefix test: ``/a/b`` matches ``/a/b`` and ``/a/b/c`` but not ``/a/bc``."""
    if path == prefix:
        return True
    prefix_with_sep = prefix if prefix.endswith("/") else f"{prefix}/"
    return path.startswith(prefix_with_sep)


def route(event: FsEvent, config: RuntimeConfig) -> list[ScanRequest]:
    """Map a filesystem event to one ``ScanRequest`` per matching ``(server, folder)`` route.

    A route matches when its ``path`` is a segment-prefix of ``event.path``, the event path is
    not inside an ignored directory, and the file extension matches the route's extension set
    (empty set => all). ``scan_path``/``top_folder``/``scan_key`` are computed per the route's
    ``scan_mode`` (invariant 2).
    """
    if is_ignored(event.path, config.ignore_dirs):
        return []

    requests: list[ScanRequest] = []
    for folder_route in config.routes:
        if not _is_path_prefix(folder_route.path, event.path):
            continue
        if not extension_matches(event.path, folder_route.extensions):
            continue

        if folder_route.scan_mode is ScanMode.targeted:
            scan_path, top_folder = compute_scan_path(folder_route.path, event.path)
            scan_key = scan_path
        else:
            scan_path = None
            top_folder = None
            scan_key = f"lib:{folder_route.library_id}"

        requests.append(
            ScanRequest(
                server_id=folder_route.server_id,
                server_name=folder_route.server_name,
                scan_mode=folder_route.scan_mode,
                scan_path=scan_path,
                library_id=folder_route.library_id,
                scan_key=scan_key,
                event_type=event.event_type,
                file_path=event.path,
                top_folder=top_folder,
            )
        )
    return requests
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/pipeline/test_router.py -v`
Expected: `13 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `ruff check mediascanmonitor/pipeline/router.py tests/pipeline/test_router.py && ruff format --check mediascanmonitor/pipeline/router.py tests/pipeline/test_router.py && mypy mediascanmonitor/pipeline/router.py`
Expected: `All checks passed!`, no format diff, `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/pipeline/router.py tests/pipeline/test_router.py
git commit -m "feat(pipeline): add router with scan-path computation and segment-aware fan-out"
```

---

## Task 4: `debounce.py` — `Debouncer` with a deterministic fake clock

**Files:**
- Create: `tests/pipeline/clock.py` (fake-clock helper)
- Create: `mediascanmonitor/pipeline/debounce.py`
- Test: `tests/pipeline/test_debounce.py`

- [ ] **Step 1: Write the fake-clock helper**

The `Debouncer` takes an injectable `sleep` callable. The fake clock makes `sleep` resolve only
when virtual time is advanced, so tests are deterministic with **no real waiting**.

Design notes (why it is shaped this way):
- `sleep(delay)` registers a future at `now + delay` and awaits it. On cancellation it removes
  its own entry so a re-armed timer leaves no stale wakeups.
- `advance(seconds)` **first** yields (`_settle`) so any just-created `asyncio.Task`s reach
  their `sleep()` registration **before** time moves — otherwise a task created but not yet run
  would register its deadline *after* the bump and never fire. It then advances virtual time,
  wakes every due future, and yields again so woken coroutines run to completion.
- `_settle` yields a small fixed number of times via real `asyncio.sleep(0)` — that is a single
  event-loop turn each, not a timed wait, so it stays deterministic.

Create `tests/pipeline/clock.py`:

```python
from __future__ import annotations

import asyncio


class ManualClock:
    """A controllable virtual clock whose ``sleep`` only resolves when ``advance`` is called.

    Inject ``clock.sleep`` into ``Debouncer(..., sleep=clock.sleep)`` and drive timers with
    ``await clock.advance(seconds)``. No real wall-clock time elapses.
    """

    def __init__(self) -> None:
        self._now: float = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        entry: tuple[float, asyncio.Future[None]] = (self._now + delay, future)
        self._sleepers.append(entry)
        try:
            await future
        except asyncio.CancelledError:
            if entry in self._sleepers:
                self._sleepers.remove(entry)
            raise

    async def advance(self, seconds: float) -> None:
        # Let freshly-scheduled tasks reach their sleep() registration first.
        await self._settle()
        self._now += seconds
        for deadline, future in list(self._sleepers):
            if deadline <= self._now and not future.done():
                future.set_result(None)
        self._sleepers = [(d, f) for (d, f) in self._sleepers if not f.done()]
        # Let woken coroutines run to their next suspension point / completion.
        await self._settle()

    @staticmethod
    async def _settle() -> None:
        for _ in range(5):
            await asyncio.sleep(0)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/pipeline/test_debounce.py`:

```python
from __future__ import annotations

from mediascanmonitor.db.models import DebounceMode, ScanMode
from mediascanmonitor.pipeline.debounce import Debouncer
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from tests.pipeline.clock import ManualClock
from tests.pipeline.factories import make_server_runtime


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ScanRequest] = []

    async def __call__(self, req: ScanRequest) -> None:
        self.calls.append(req)


def _req(server_id: int, scan_key: str, *, file_path: str = "/data/tv/Show/ep.mkv") -> ScanRequest:
    return ScanRequest(
        server_id=server_id,
        server_name="plex-1",
        scan_mode=ScanMode.targeted,
        scan_path=scan_key,
        library_id="2",
        scan_key=scan_key,
        event_type=FsEventType.created,
        file_path=file_path,
        top_folder="Show",
    )


async def test_off_mode_dispatches_every_event_immediately() -> None:
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.off)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    for _ in range(3):
        await debouncer.submit(_req(1, "/data/tv/Show"))

    # off mode bypasses the timer: all three dispatched without advancing the clock.
    assert len(recorder.calls) == 3
    await debouncer.aclose()


async def test_trailing_collapses_a_burst_into_one_dispatch() -> None:
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.trailing,
                                      debounce_window_seconds=30)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    for i in range(5):
        await debouncer.submit(_req(1, "/data/tv/Show", file_path=f"/data/tv/Show/ep{i}.mkv"))

    assert recorder.calls == []          # nothing fires before the window elapses
    await clock.advance(30)
    assert len(recorder.calls) == 1      # one dispatch for the whole burst
    assert recorder.calls[0].file_path == "/data/tv/Show/ep4.mkv"  # the most-recent request
    await debouncer.aclose()


async def test_trailing_distinct_scan_keys_debounce_independently() -> None:
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.trailing,
                                      debounce_window_seconds=30)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/ShowA"))
    await debouncer.submit(_req(1, "/data/tv/ShowB"))

    await clock.advance(30)
    assert {r.scan_key for r in recorder.calls} == {"/data/tv/ShowA", "/data/tv/ShowB"}
    assert len(recorder.calls) == 2
    await debouncer.aclose()


async def test_trailing_resets_the_window_on_each_event() -> None:
    # Proves reset-on-each-event semantics (not a fixed window from the first event).
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.trailing,
                                      debounce_window_seconds=30)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/Show", file_path="/data/tv/Show/first.mkv"))
    await clock.advance(10)                       # 10s in, first timer would fire at 30
    assert recorder.calls == []

    await debouncer.submit(_req(1, "/data/tv/Show", file_path="/data/tv/Show/second.mkv"))
    await clock.advance(20)                       # now 30s from first event, 20s from second
    assert recorder.calls == []                   # NOT fired -> the window was reset by event 2

    await clock.advance(10)                        # now 30s from the second event
    assert len(recorder.calls) == 1
    assert recorder.calls[0].file_path == "/data/tv/Show/second.mkv"
    await debouncer.aclose()


async def test_aclose_cancels_pending_timers_without_dispatching() -> None:
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.trailing,
                                      debounce_window_seconds=30)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/Show"))
    await debouncer.aclose()
    await clock.advance(30)
    assert recorder.calls == []          # pending timer was cancelled and dropped


async def test_unknown_server_falls_back_to_immediate_dispatch() -> None:
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, {}, sleep=clock.sleep)  # no servers registered

    await debouncer.submit(_req(99, "/data/tv/Show"))
    assert len(recorder.calls) == 1      # fail-open: deliver rather than silently drop
    await debouncer.aclose()


async def test_update_servers_drops_timers_for_removed_servers() -> None:
    # Engine.rebuild swaps the server map in place; a removed server's pending timer is dropped.
    servers = {
        1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.trailing,
                               debounce_window_seconds=30),
        2: make_server_runtime(server_id=2, debounce_mode=DebounceMode.trailing,
                               debounce_window_seconds=30),
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/ShowA"))
    await debouncer.submit(_req(2, "/data/tv/ShowB"))

    debouncer.update_servers({1: servers[1]})   # server 2 removed on rebuild
    await clock.advance(30)

    assert {r.server_id for r in recorder.calls} == {1}  # server 2's timer cancelled, not fired
    await debouncer.aclose()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/pipeline/test_debounce.py -v`
Expected: collection error / `ModuleNotFoundError: No module named
'mediascanmonitor.pipeline.debounce'`.

- [ ] **Step 4: Write the implementation**

Create `mediascanmonitor/pipeline/debounce.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import DebounceMode
from mediascanmonitor.pipeline.events import ScanRequest


class Debouncer:
    """Per-server debounce applied after routing.

    ``off``      -> await ``dispatch(req)`` immediately (no coalescing).
    ``trailing`` -> coalesce per ``(server_id, scan_key)`` with classic trailing-edge semantics:
                    each ``submit`` cancels any pending timer for the key and arms a fresh
                    ``window``-second timer; the dispatch fires once, ``window`` seconds after
                    the most recent event, carrying the most recent request.

    A server id with no registered ``ServerRuntime`` fails open (immediate dispatch) rather than
    silently dropping the event. ``sleep`` is injectable so tests drive a fake clock.
    """

    def __init__(
        self,
        dispatch: Callable[[ScanRequest], Awaitable[None]],
        servers: dict[int, ServerRuntime],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._dispatch = dispatch
        self._servers = servers
        self._sleep = sleep
        self._timers: dict[tuple[int, str], asyncio.Task[None]] = {}

    async def submit(self, req: ScanRequest) -> None:
        server = self._servers.get(req.server_id)
        if server is None or server.debounce_mode is DebounceMode.off:
            await self._dispatch(req)
            return

        window = float(server.debounce_window_seconds)
        key = (req.server_id, req.scan_key)
        pending = self._timers.get(key)
        if pending is not None:
            pending.cancel()
        self._timers[key] = asyncio.create_task(self._fire_after(key, req, window))

    def update_servers(self, servers: dict[int, ServerRuntime]) -> None:
        """Swap the per-server policy map in place on ``Engine.rebuild`` (contract §9/§10),
        keeping this Debouncer instance and its pending timers. A pending
        ``(server_id, scan_key)`` whose server is **gone** from ``servers`` is cancelled (the
        server was disabled/deleted — do not dispatch). Survivors keep their armed timer; the
        new window length is read only when a key next (re)arms, not retroactively.
        """
        self._servers = servers
        for key in list(self._timers):
            if key[0] not in servers:
                task = self._timers.pop(key, None)
                if task is not None:
                    task.cancel()

    async def _fire_after(self, key: tuple[int, str], req: ScanRequest, window: float) -> None:
        try:
            await self._sleep(window)
        except asyncio.CancelledError:
            return
        # Only fire if we are still the active timer for this key (guards a re-arm that landed
        # in the same loop turn the window elapsed -> never double-dispatch).
        if self._timers.get(key) is not asyncio.current_task():
            return
        del self._timers[key]
        await self._dispatch(req)

    async def aclose(self) -> None:
        timers = list(self._timers.values())
        self._timers.clear()
        for task in timers:
            task.cancel()
        for task in timers:
            try:
                await task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/pipeline/test_debounce.py -v`
Expected: `7 passed`.

- [ ] **Step 6: Lint and type-check**

Run: `ruff check mediascanmonitor/pipeline/debounce.py tests/pipeline/clock.py tests/pipeline/test_debounce.py && ruff format --check mediascanmonitor/pipeline/debounce.py tests/pipeline/clock.py tests/pipeline/test_debounce.py && mypy mediascanmonitor/pipeline/debounce.py`
Expected: `All checks passed!`, no format diff, `Success: no issues found in 1 source file`.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/pipeline/debounce.py tests/pipeline/clock.py tests/pipeline/test_debounce.py
git commit -m "feat(pipeline): add per-server trailing/off debouncer with injectable clock"
```

---

## Task 5: `dispatcher.py` — `Dispatcher` with failure isolation

**Files:**
- Create: `mediascanmonitor/pipeline/dispatcher.py`
- Test: `tests/pipeline/test_dispatcher.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_dispatcher.py`:

```python
from __future__ import annotations

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.dispatcher import Dispatcher
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult


def _req(server_id: int) -> ScanRequest:
    return ScanRequest(
        server_id=server_id,
        server_name="plex-1",
        scan_mode=ScanMode.targeted,
        scan_path="/data/tv/Show",
        library_id="2",
        scan_key="/data/tv/Show",
        event_type=FsEventType.created,
        file_path="/data/tv/Show/ep.mkv",
        top_folder="Show",
    )


class OkAdapter(ServerAdapter):
    server_type = ServerType.plex
    supported_scan_modes = frozenset({ScanMode.targeted, ScanMode.library})

    def __init__(self) -> None:  # no httpx client needed for this fake
        self.calls: list[ScanRequest] = []

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        self.calls.append(req)
        return TriggerResult(ok=True, status_code=200, detail="ok")

    async def test(self) -> TestResult:
        return TestResult(ok=True, detail="ok")


class FaultyAdapter(ServerAdapter):
    server_type = ServerType.plex
    supported_scan_modes = frozenset({ScanMode.targeted})

    def __init__(self) -> None:
        pass

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        raise RuntimeError("boom")

    async def test(self) -> TestResult:
        return TestResult(ok=False, detail="boom")


async def test_dispatch_calls_matching_adapter_and_returns_its_result() -> None:
    adapter = OkAdapter()
    dispatcher = Dispatcher({1: adapter})

    result = await dispatcher.dispatch(_req(1))

    assert result.ok is True
    assert result.status_code == 200
    assert len(adapter.calls) == 1


async def test_dispatch_isolates_adapter_exceptions() -> None:
    dispatcher = Dispatcher({1: FaultyAdapter()})

    result = await dispatcher.dispatch(_req(1))  # must NOT raise

    assert result.ok is False
    assert result.status_code is None
    assert "boom" in result.detail


async def test_dispatch_unknown_server_id_returns_failure_not_raise() -> None:
    dispatcher = Dispatcher({1: OkAdapter()})

    result = await dispatcher.dispatch(_req(999))

    assert result.ok is False
    assert result.status_code is None
    assert "999" in result.detail


async def test_set_adapters_swaps_the_adapter_map() -> None:
    old = OkAdapter()
    dispatcher = Dispatcher({1: old})

    new = OkAdapter()
    dispatcher.set_adapters({2: new})

    # Old id no longer routes; new id does.
    miss = await dispatcher.dispatch(_req(1))
    assert miss.ok is False
    hit = await dispatcher.dispatch(_req(2))
    assert hit.ok is True
    assert len(old.calls) == 0
    assert len(new.calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/pipeline/test_dispatcher.py -v`
Expected: collection error / `ModuleNotFoundError: No module named
'mediascanmonitor.pipeline.dispatcher'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/pipeline/dispatcher.py`:

```python
from __future__ import annotations

import structlog

from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TriggerResult

log = structlog.get_logger(__name__)


class Dispatcher:
    """Fan a single ``ScanRequest`` to its server's adapter, isolating all failures.

    Invariant 6: one bad server never raises out of ``dispatch`` or aborts the event loop. A
    missing adapter or an adapter exception becomes ``TriggerResult(ok=False, ...)``. Only
    ``Exception`` is caught so ``asyncio.CancelledError`` still propagates for clean shutdown.
    """

    def __init__(self, adapters: dict[int, ServerAdapter]) -> None:
        self._adapters = adapters

    async def dispatch(self, req: ScanRequest) -> TriggerResult:
        adapter = self._adapters.get(req.server_id)
        if adapter is None:
            log.warning(
                "dispatch.no_adapter",
                server_id=req.server_id,
                server_name=req.server_name,
                scan_key=req.scan_key,
            )
            return TriggerResult(
                ok=False,
                status_code=None,
                detail=f"no adapter for server_id={req.server_id}",
            )
        try:
            return await adapter.trigger(req)
        except Exception as exc:  # isolate: never propagate a per-server failure
            log.warning(
                "dispatch.adapter_error",
                server_id=req.server_id,
                server_name=req.server_name,
                scan_key=req.scan_key,
                error=repr(exc),
            )
            return TriggerResult(ok=False, status_code=None, detail=f"adapter raised: {exc!r}")

    def set_adapters(self, adapters: dict[int, ServerAdapter]) -> None:
        """Swap the adapter map atomically (used by ``Engine.rebuild`` on config change)."""
        self._adapters = adapters
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/pipeline/test_dispatcher.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `ruff check mediascanmonitor/pipeline/dispatcher.py tests/pipeline/test_dispatcher.py && ruff format --check mediascanmonitor/pipeline/dispatcher.py tests/pipeline/test_dispatcher.py && mypy mediascanmonitor/pipeline/dispatcher.py`
Expected: `All checks passed!`, no format diff, `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/pipeline/dispatcher.py tests/pipeline/test_dispatcher.py
git commit -m "feat(pipeline): add dispatcher with per-server failure isolation and rebuild swap"
```

---

## Task 6: Full-suite verification

Confirm the whole pipeline sub-package is green, typed, and lint-clean together.

- [ ] **Step 1: Run the full pipeline test suite**

Run: `pytest tests/pipeline/ -v`
Expected: `36 passed` (12 filters + 13 router + 7 debounce + 4 dispatcher).

- [ ] **Step 2: Type-check the whole package**

Run: `mypy mediascanmonitor`
Expected: `Success: no issues found in N source files` (no errors from the four pipeline
modules).

- [ ] **Step 3: Lint and format-check the package and pipeline tests**

Run: `ruff check mediascanmonitor tests/pipeline && ruff format --check mediascanmonitor tests/pipeline`
Expected: `All checks passed!` and no formatting diff.

- [ ] **Step 4: Run the entire repo test suite (no regressions)**

Run: `pytest`
Expected: all tests pass (existing `tests/test_cli.py` plus the new `tests/pipeline/` suite).

- [ ] **Step 5: Commit (only if any fixups were needed)**

```bash
git add -A
git commit -m "test(pipeline): verify full pipeline suite, types, and lint clean"
```

---

## Self-Review (performed against the contract §9 + cross-plan invariants)

**1. Spec coverage**

| Contract §9 / prompt item | Task |
|---|---|
| `filters.is_ignored` (segment-aware) | Task 2 |
| `filters.extension_matches` (empty set ⇒ True, case-insensitive) | Task 2 (invariant 1) |
| `router.compute_scan_path` (two-levels-deep + directly-in-root) | Task 3 |
| `router.route` (segment prefix, ignore, ext match, fan-out, scan_key) | Task 3 (invariants 2, 5) |
| Two servers, same path, different ext sets → correct subscriber set | Task 3 |
| Prefix correctness (`/a/bc` not matched by `/a/b`) | Task 3 (invariant 5) |
| Targeted vs library `scan_key` (`lib:{id}`), library `scan_path=None` | Task 3 (invariant 2) |
| `Debouncer` off ⇒ immediate; trailing ⇒ coalesce; injectable sleep; `aclose` | Task 4 |
| `Debouncer.update_servers` (rebuild swap; drop removed-server timers) | Task 4 (contract §9/§10) |
| Burst → one dispatch; independent keys; window reset; `aclose` deterministic | Task 4 (risk #5) |
| Fake-clock helper full code | Task 4 (`tests/pipeline/clock.py`) |
| `Dispatcher` lookup, exception isolation (never raises), `set_adapters` | Task 5 (invariant 6) |
| Unknown server_id handled gracefully | Task 5 |

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every code
step contains complete, runnable code. ✓

**3. Type consistency:** Field/method names cross-checked against contract: `ScanRequest`
fields (`server_id, server_name, scan_mode, scan_path, library_id, scan_key, event_type,
file_path, top_folder`), `FolderRoute` fields (`server_id, server_name, path, extensions,
library_id, scan_mode`), `ServerRuntime` fields (incl. `type`, `debounce_mode`,
`debounce_window_seconds`), `RuntimeConfig` (`watch_paths, routes, servers, ignore_dirs`),
`TriggerResult(ok, status_code, detail)`, `ServerAdapter.trigger/test`. `Debouncer.__init__`,
`Debouncer.update_servers`, and `Dispatcher.dispatch/set_adapters` match §9 verbatim. ✓

**Contract deviations:** none. Two contract-permitted choices were *resolved* (not changed):
trailing = reset-on-each-event; `aclose` = cancel-and-drop. Both are documented above and
pinned by Task 4 tests.

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-17-phase1-05-pipeline.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between
   tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session via `superpowers:executing-plans`,
   batch execution with checkpoints.

Which approach?
