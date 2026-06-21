# Phase 3 — Sub-plan 03: Engine Integration (events bus, gate-recovery, `run` web wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking. Complete code in every step — no placeholders, no diffs-by-prose.

**Goal:** Wire the existing async `Engine` into the Phase 3 web layer and make the inotify gate
**recoverable without a restart**. Three seams: (§G) the engine publishes every dispatch to the
`EventsBus`; (§I) the engine moves `blocked ↔ running` on a gate/config change via a dynamic
zero-root watcher swap; (§H/§J) `web/api/system.py` exposes liveness/readiness/status + the
user-facing gate controls, and `media-scan-monitor run` (without `--no-web`) serves uvicorn + the
engine on one event loop.

**Architecture:** This is the engine↔web integration sub-plan. It is **additive** to the frozen
Phase 1/2 contract: the only Phase-1 module it edits is `engine.py`, and `events_bus=None` reproduces
today's dispatch byte-for-byte. The whole gate-recovery trick is that **`blocked` is not "watcher
detached"** — for the web path it means the watcher is attached but watching **zero roots** while the
`async for event in self._watcher.events()` consume loop stays alive and idle. Every gate transition
is therefore the already-proven dynamic `set_roots` swap plus a `self.state` flag flip — no new
supervised loop, no wake `Event`, no parking primitive. The headless path keeps the Bash-style
block→exit-3 behavior by early-returning instead of looping.

**Tech Stack:** Python 3.14, `fastapi==0.137.1`, `uvicorn[standard]==0.49.0`, `pydantic` (via
FastAPI), `httpx==0.28.1` (TestClient transport), `pytest==9.1.0` + `pytest-asyncio==1.4.0`,
`structlog`. `ruff`/`mypy --strict` clean, line length 100. PEP 649 annotations — **no**
`from __future__ import annotations`.

## Global Constraints

- **PEP 649 (rule 3):** never add `from __future__ import annotations`; leave forward refs unquoted.
  Any name used in a runtime-introspected annotation (Pydantic `StatusRead`) must be importable at
  runtime — none here are hidden behind `TYPE_CHECKING`.
- **Enums subclass `StrEnum`** (already true for `EngineState`/`ScanMode`/`FsEventType`). `str(member)`
  is the bare value; prefer `.value` when emitting the bare string (we use `.value` for `EventRecord`
  / `StatusRead`).
- **Ruff `select` is exactly `E,F,I,UP,B,C4,SIM,RUF`** (per-file-ignore `B` under `tests/**`). `BLE`,
  `SLF001` are **not** selected — do not add `# noqa` for them (it trips `RUF100`). A bare
  `except Exception:` is fine here. `# noqa: F401` on a self-registration import is valid and stays.
- **`mediascanmonitor` is first-party for isort** — separate third-party from first-party imports with
  a blank line.
- **`mypy --strict` clean**, line length 100.
- **Async discipline (rule 4):** no blocking call in the event loop. Every `Repo` call and every
  `check_watch_limit` call from a coroutine goes through `await asyncio.to_thread(...)`. The engine's
  `_gate_ok` helper bridges both. `bus.publish(...)` is **sync and non-blocking** by contract — safe
  to call from `Engine._dispatch`.
- **Secrets never logged / never in any record (rule 5, invariant 1):** `EventRecord` carries no
  secret field; `StatusRead` carries no secret. Do not log tokens or rendered headers.
- **The gate never wedges the web layer (invariant 5):** the web app always serves; only the engine
  task is gated; `/health` is always `200`; empty config is always `ready`.
- **Use `datetime.now(UTC)`** (timezone-aware) for `EventRecord.ts` — never a naive `utcnow()`.
- **One existing engine test changes:**
  `tests/test_engine.py::test_blocked_when_watch_limit_insufficient_and_enforced` must call
  `engine.start(park_when_blocked=False)` (it models the headless contract). Every other
  engine/headless test is untouched because `events_bus=None` and the gate-ok paths behave exactly as
  before. **One existing CLI test changes:** `test_run_without_no_web_prints_phase3_message` (the
  Phase-1 exit-2 stub) is replaced by a "serves" test.
- **Tooling is not on `PATH`.** Run **once per shell**: `export PATH="$PWD/.venv/bin:$PATH"`. After
  that, every command below uses bare `pytest` / `ruff` / `mypy`.

## Assumed prerequisites (from sub-plans 01 + 02 — canonical execution order 01→02→03)

- **`mediascanmonitor/observ/events_bus.py`** exists with the frozen `EventRecord` dataclass and
  `EventsBus` class (contract §G). This sub-plan **only wires the Engine** to it — it does **not**
  create or modify the class.
- **`mediascanmonitor/web/app.py::create_app(repo, engine, events_bus, *, session_secret)`**,
  `web/deps.py` (`get_repo`, `get_engine`, `require_api_auth`), `web/auth.py::bootstrap_password`
  exist (sub-plan 01).
- **`mediascanmonitor/web/rebuild.py::rebuild_engine(engine)`** exists (sub-plan 02, contract §F):
  `await engine.rebuild()` tolerant of a not-started engine (catches `RuntimeError`, logs
  `web.rebuild_skipped`).
- **`tests/web/conftest.py`** (sub-plan 01) provides fixtures: `repo` (real in-memory `Repo`),
  `events_bus`, `engine` (a **FakeEngine** stub exposing a settable `.state: EngineState`, a settable
  `.watch_limit: WatchLimitStatus | None`, and an `async def rebuild()` that counts calls), `app`,
  `client` (unauth `TestClient`), `auth_client` (session pre-authed), `aclient` (async).
  - For the §H route tests (Task 4) the FakeEngine is sufficient — we set `.state`/`.watch_limit`
    directly and assert `rebuild` was called.
  - For the §I gate-recovery tests (Task 2) the FakeEngine is **not** enough: those build a **real**
    `Engine(repo, watcher=FakeWatcher(), events_bus=...)` in-test with `check_watch_limit`
    monkeypatched, so the four real state transitions are observable. This is stated again in Task 2.

## File Structure

```
mediascanmonitor/
  engine.py                 # MODIFY: events_bus param + _dispatch publish (§G); _gate_ok + start/rebuild gate-recovery (§I)
  cli.py                    # MODIFY: serve_headless park_when_blocked=False; _load_key helper; _cmd_run web path (§J)
  web/
    app.py                  # MODIFY: include system routers (one merge point)
    server.py               # CREATE: serve_web() — uvicorn + engine on one loop (§J)
    api/
      __init__.py           # exists (sub-plan 02)
      system.py             # CREATE: StatusRead + /health /ready /api/status + gate routes (§H)
tests/
  test_engine.py            # MODIFY: 1 existing test + new publish/gate-recovery tests
  test_cli.py               # MODIFY: replace exit-2 test; add serve_web wiring + headless-blocked tests
  web/
    test_system.py          # CREATE: system route tests via the harness
    test_server.py          # CREATE: serve_web lifecycle test (no real socket bind)
```

---

### Task 1: Engine events-bus publish wiring (§G)

Add the `events_bus` ctor param and publish an `EventRecord` from `_dispatch` after each
`dispatcher.dispatch(req)`. `events_bus=None` ⇒ today's behavior exactly.

**Files:**
- Modify: `mediascanmonitor/engine.py`
- Test: `tests/test_engine.py` (add one test; existing tests unchanged in this task)

**Interfaces:**
- Consumes: `EventsBus`, `EventRecord` (`mediascanmonitor/observ/events_bus.py`); `TriggerResult`
  (`mediascanmonitor/servers/base.py`, returned by `Dispatcher.dispatch`); `ScanRequest`
  (`mediascanmonitor/pipeline/events.py`); `datetime`, `UTC` (stdlib).
- Produces: `Engine.__init__(repo, *, watcher=None, events_bus: EventsBus | None = None)`; an
  `EventRecord` published per dispatch when a bus is present.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py` (add the imports near the existing import block):

```python
from mediascanmonitor.observ.events_bus import EventsBus


async def test_dispatch_publishes_event_record_when_bus_present(
    monkeypatch: pytest.MonkeyPatch, stub_repo: Repo
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2", extensions={"mkv"})
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    bus = EventsBus()
    watcher = FakeWatcher()
    engine = Engine(stub_repo, watcher=watcher, events_bus=bus)
    await watcher.emit(
        FsEvent(path="/data/tv/Shoresy/ep1.mkv", event_type=FsEventType.created, is_dir=False)
    )
    await watcher.aclose()
    await engine.start()
    await engine.aclose()

    records = bus.recent()
    assert len(records) == 1
    rec = records[0]
    assert rec.server_id == 1
    assert rec.server_name == "plex"
    assert rec.scan_mode == ScanMode.targeted.value
    assert rec.scan_key == "/data/tv/Shoresy"
    assert rec.scan_path == "/data/tv/Shoresy"
    assert rec.library_id == "2"
    assert rec.event_type == FsEventType.created.value
    assert rec.file_path == "/data/tv/Shoresy/ep1.mkv"
    assert rec.ok is True
    assert rec.status_code == 200
    assert rec.ts.endswith("+00:00")  # ISO-8601 UTC, timezone-aware
```

- [ ] **Step 2: Run it — expect FAIL**

```bash
pytest tests/test_engine.py::test_dispatch_publishes_event_record_when_bus_present -q
```

Expected: FAIL — `TypeError: Engine.__init__() got an unexpected keyword argument 'events_bus'`.

- [ ] **Step 3: Minimal implementation**

In `mediascanmonitor/engine.py`, add the imports (keep first-party grouping — blank line between
stdlib/third-party and first-party):

```python
from datetime import UTC, datetime
```

and, in the first-party block:

```python
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
```

Extend `__init__` to accept and store the bus:

```python
    def __init__(
        self,
        repo: Repo,
        *,
        watcher: WatcherBackend | None = None,
        events_bus: EventsBus | None = None,
    ) -> None:
        self._repo = repo
        self._watcher: WatcherBackend | None = watcher
        self._events_bus = events_bus
        self._config: RuntimeConfig | None = None
        self._dispatcher: Dispatcher | None = None
        self._debouncer: Debouncer | None = None
        self._clients: dict[int, httpx.AsyncClient] = {}
        self._started = False
        self._lock = asyncio.Lock()
        self.state: EngineState = EngineState.stopped
        self.watch_limit: WatchLimitStatus | None = None
```

Replace `_dispatch` (capture the result, publish if a bus is set):

```python
    async def _dispatch(self, req: ScanRequest) -> None:
        """Adapter for the Debouncer's ``Awaitable[None]`` dispatch signature.

        Captures the dispatch outcome and, if an events bus is wired, publishes a
        redacted ``EventRecord`` (no secret field — invariant 1). Without a bus this
        reproduces the Phase 1/2 behavior exactly.
        """
        dispatcher = self._dispatcher
        if dispatcher is None:
            return
        result = await dispatcher.dispatch(req)
        bus = self._events_bus
        if bus is not None:
            bus.publish(
                EventRecord(
                    ts=datetime.now(UTC).isoformat(),
                    server_id=req.server_id,
                    server_name=req.server_name,
                    scan_mode=req.scan_mode.value,
                    scan_key=req.scan_key,
                    scan_path=req.scan_path,
                    library_id=req.library_id,
                    event_type=req.event_type.value,
                    file_path=req.file_path,
                    ok=result.ok,
                    status_code=result.status_code,
                    detail=result.detail,
                )
            )
```

- [ ] **Step 4: Run it — expect PASS**

```bash
pytest tests/test_engine.py -q
```

Expected: PASS — the new test plus every existing engine test (they construct `Engine(repo, ...)`
with no bus, so `_dispatch` skips the publish branch).

- [ ] **Step 5: Lint + type-check the touched files**

```bash
ruff check mediascanmonitor/engine.py tests/test_engine.py && mypy mediascanmonitor/engine.py
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/engine.py tests/test_engine.py
git commit -m "feat(engine): publish redacted EventRecord per dispatch when an events bus is wired"
```

---

### Task 2: Engine gate-recovery — `_gate_ok` + `start(park_when_blocked=...)` + `rebuild()` (§I)

**This is the highest-risk change in the phase.** Refactor the gate so `blocked ↔ running` happens
without a restart, driven by `FakeWatcher` + monkeypatched `check_watch_limit`. Public signatures stay
stable except the additive `park_when_blocked` keyword on `start`.

**Files:**
- Modify: `mediascanmonitor/engine.py`
- Test: `tests/test_engine.py` (update 1 existing test; add 4 transition tests)

**Interfaces:**
- Consumes: `RuntimeConfig` (`config/runtime.py`), `check_watch_limit`/`WatchLimitStatus`
  (`watcher/watch_limit.py`), `Repo.get_setting` (sync — via `asyncio.to_thread`).
- Produces:
  ```python
  async def _gate_ok(self, config: RuntimeConfig) -> bool: ...     # sets self.watch_limit (to_thread)
  async def start(self, *, park_when_blocked: bool = True) -> None: ...
  async def rebuild(self) -> None: ...                            # unchanged signature
  ```

The four transitions `rebuild()` must cover without raising:

| from → to | trigger | action inside `rebuild()` |
|---|---|---|
| `blocked → running` | kernel limit raised (+ recheck/rebuild) **or** `inotify_gate=off` | `_gate_ok` True → `set_roots(watch_paths)`, `state=running` |
| `running → blocked` | new config outgrows the limit under `enforce` | `_gate_ok` False → `set_roots(∅)`, `state=blocked` |
| `running → running` | ordinary config edit, gate still ok | dynamic root diff (today's behavior) |
| `blocked → blocked` | edit while still over the limit | `set_roots(∅)` stays, `state=blocked` |

The four tests below directly exercise `blocked→running` (×2 — policy flip and limit-raise),
`running→blocked`, and the headless early-return. `running→running` is already covered by the
existing `test_rebuild_adds_then_removes_roots_and_reroutes`, and `blocked→blocked` exercises the
identical `_gate_ok`-False branch as `running→blocked` (`set_roots(∅)` + `state=blocked`) — so all
four rebuild branches are exercised, not all by a brand-new test. (Add an explicit `blocked→blocked`
assertion if you want belt-and-suspenders; it is not required for branch coverage.)

- [ ] **Step 1: Update the one existing test, then write the new failing tests**

In `tests/test_engine.py`, change `test_blocked_when_watch_limit_insufficient_and_enforced` so its
`start()` call passes `park_when_blocked=False` (it models the headless contract — only headless
early-returns on a blocked gate). Replace the single line:

```python
    await engine.start()  # returns immediately: blocked, watcher never attached
```

with:

```python
    await engine.start(park_when_blocked=False)  # headless contract: block -> return, no roots
```

The rest of that test (asserts `state is blocked`, `watch_limit.ok is False`,
`roots_history == []`) stays as-is.

Then append the four transition tests:

```python
class _MutRepo:
    """Repo stub with a settable inotify_gate policy (start/rebuild read only get_setting)."""

    def __init__(self, gate: str = "enforce") -> None:
        self.gate = gate

    def get_setting(self, key: str) -> str | None:
        return self.gate


def _ok(_paths: object, _ignore: object) -> WatchLimitStatus:
    return WatchLimitStatus(current=1_000_000, dirs=0, needed=0, recommended=0, ok=True)


def _not_ok(_paths: object, _ignore: object) -> WatchLimitStatus:
    return WatchLimitStatus(current=10, dirs=100, needed=120, recommended=144, ok=False)


async def test_blocked_to_running_on_policy_flip_to_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)
    monkeypatch.setattr(engine_module, "check_watch_limit", _not_ok)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    repo = _MutRepo(gate="enforce")
    watcher = FakeWatcher()
    engine = Engine(cast(Repo, repo), watcher=watcher)
    start_task = asyncio.create_task(engine.start())  # default park_when_blocked=True
    await wait_for(lambda: engine.state is EngineState.blocked)
    assert watcher.current_roots == set()  # parked at zero roots, loop alive

    # Flip the policy to off and rebuild -> gate now passes -> roots attach, state running.
    repo.gate = "off"
    await engine.rebuild()
    assert engine.state is EngineState.running
    assert watcher.current_roots == {"/data/tv"}

    await engine.aclose()
    await start_task


async def test_blocked_to_running_on_limit_raised_then_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)
    monkeypatch.setattr(engine_module, "check_watch_limit", _not_ok)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    repo = _MutRepo(gate="enforce")
    watcher = FakeWatcher()
    engine = Engine(cast(Repo, repo), watcher=watcher)
    start_task = asyncio.create_task(engine.start())
    await wait_for(lambda: engine.state is EngineState.blocked)

    # Operator raised fs.inotify.max_user_watches out of band -> recheck re-evaluates the gate.
    monkeypatch.setattr(engine_module, "check_watch_limit", _ok)
    await engine.rebuild()
    assert engine.state is EngineState.running
    assert watcher.current_roots == {"/data/tv"}
    assert engine.watch_limit is not None and engine.watch_limit.ok is True

    await engine.aclose()
    await start_task


async def test_running_to_blocked_on_config_outgrowth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)
    monkeypatch.setattr(engine_module, "check_watch_limit", _ok)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    repo = _MutRepo(gate="enforce")
    watcher = FakeWatcher()
    engine = Engine(cast(Repo, repo), watcher=watcher)
    start_task = asyncio.create_task(engine.start())
    await wait_for(lambda: engine.state is EngineState.running)
    assert watcher.current_roots == {"/data/tv"}

    # The new config outgrows the limit under enforce -> rebuild parks at zero roots.
    monkeypatch.setattr(engine_module, "check_watch_limit", _not_ok)
    await engine.rebuild()
    assert engine.state is EngineState.blocked
    assert watcher.current_roots == set()

    await engine.aclose()
    await start_task


async def test_headless_park_false_blocked_returns_without_attaching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[int, RecordingAdapter] = {}
    _patch_factories(monkeypatch, created)
    monkeypatch.setattr(engine_module, "check_watch_limit", _not_ok)

    server = make_server_runtime(1, name="plex", debounce=DebounceMode.off)
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        engine_module, "build_runtime_config", lambda repo: make_config([route], [server])
    )

    watcher = FakeWatcher()
    engine = Engine(cast(Repo, _MutRepo(gate="enforce")), watcher=watcher)
    await engine.start(park_when_blocked=False)  # returns immediately, no loop

    assert engine.state is EngineState.blocked
    assert watcher.roots_history == []  # set_roots never called -> headless exit-3 contract
    await engine.aclose()
```

- [ ] **Step 2: Run them — expect FAIL**

```bash
pytest tests/test_engine.py -q
```

Expected: FAIL — `start()` has no `park_when_blocked` keyword, and the default-park transitions
(`blocked` parked at zero roots, then `rebuild` recovering) are not yet implemented.

- [ ] **Step 3: Implement `_gate_ok`, the revised `start`, and the revised `rebuild`**

In `mediascanmonitor/engine.py`, replace the whole `start` method, add `_gate_ok`, and replace the
whole `rebuild` method. The full revised methods:

```python
    async def start(self, *, park_when_blocked: bool = True) -> None:
        """Build the runtime, wire the pipeline, consult the inotify gate, consume events.

        The pipeline (adapters/dispatcher/debouncer/watcher) is wired **unconditionally** —
        none of it needs the gate and ``_build_adapters`` does no network I/O. Then:

        * ``park_when_blocked=True`` (web): a failed gate parks the watcher at **zero roots**
          and enters the consume loop anyway; the watcher yields nothing so the engine idles
          in ``blocked`` until a later ``rebuild()`` re-points the roots. The loop is never
          interrupted, so the web layer is never wedged (invariant 5).
        * ``park_when_blocked=False`` (headless): a failed gate sets ``blocked`` and **returns**
          without attaching roots or looping — the Bash-style block→exit-3 behavior. The
          headless test asserts ``watcher.roots_history == []``.
        """
        if self._started:
            raise RuntimeError("Engine.start() called more than once")
        self._started = True
        self.state = EngineState.starting

        config = await asyncio.to_thread(build_runtime_config, self._repo)
        self._config = config

        if self._watcher is None:
            # Lazy import keeps the engine importable on non-Linux dev machines.
            from mediascanmonitor.watcher.inotify_backend import InotifyBackend

            self._watcher = InotifyBackend(config.ignore_dirs)

        adapters, clients = await self._build_adapters(config)
        self._clients = clients
        self._dispatcher = Dispatcher(adapters)
        self._debouncer = Debouncer(self._dispatch, config.servers)

        gate_ok = await self._gate_ok(config)

        if not gate_ok and not park_when_blocked:
            # Headless: do NOT attach roots or loop; serve_headless observes `blocked` and exits 3.
            self.state = EngineState.blocked
            self._log_blocked(config)
            return

        self._watcher.set_roots(set(config.watch_paths) if gate_ok else set())
        self.state = EngineState.running if gate_ok else EngineState.blocked
        if gate_ok:
            log.info(
                "engine.started",
                watch_paths=sorted(config.watch_paths),
                servers=len(config.servers),
                routes=len(config.routes),
            )
        else:
            self._log_blocked(config)

        async for event in self._watcher.events():  # idles when roots are empty (parked)
            await self._handle_event(event)

    async def _gate_ok(self, config: RuntimeConfig) -> bool:
        """Return whether the inotify gate is satisfied, and refresh ``self.watch_limit``.

        True when there is nothing to watch, OR the ``inotify_gate`` policy is ``off``, OR the
        measured watch limit is sufficient. Side effect: sets ``self.watch_limit`` (``None`` when
        there are no watch paths) so ``/api/status`` and ``/ready`` reflect live state. The repo
        read and the blocking ``check_watch_limit`` both run via ``asyncio.to_thread``.
        """
        if not config.watch_paths:
            self.watch_limit = None
            return True
        policy = await asyncio.to_thread(self._repo.get_setting, "inotify_gate")
        self.watch_limit = await asyncio.to_thread(
            check_watch_limit, config.watch_paths, config.ignore_dirs
        )
        if (policy or "enforce") == "off":
            return True
        return self.watch_limit.ok

    def _log_blocked(self, config: RuntimeConfig) -> None:
        wl = self.watch_limit
        log.error(
            "engine.blocked",
            reason="inotify watch limit too low",
            watch_paths=sorted(config.watch_paths),
            current=wl.current if wl else None,
            needed=wl.needed if wl else None,
            recommended=wl.recommended if wl else None,
            remediation=(
                f"raise fs.inotify.max_user_watches to >= {wl.recommended}" if wl else None
            ),
        )
```

Replace `rebuild` with the gate-aware version (the atomic swap block keeps **no await between its
statements**; `_gate_ok` is awaited *before* the block):

```python
    async def rebuild(self) -> None:
        """Rebuild the runtime snapshot and atomically swap it in — no restart.

        Re-evaluates the inotify gate and re-points the watcher roots, covering all four
        ``blocked ↔ running`` transitions **without raising**. The DB read (``to_thread``),
        the gate check (``to_thread``), and the old-client teardown (``aclose``) bracket a
        fully synchronous swap block, so no awaitable point splits a routing decision.
        """
        if (
            not self._started
            or self._dispatcher is None
            or self._debouncer is None
            or self._watcher is None
        ):
            raise RuntimeError("Engine.rebuild() called before start()")

        async with self._lock:
            new_config = await asyncio.to_thread(build_runtime_config, self._repo)
            new_adapters, new_clients = await self._build_adapters(new_config)
            gate_ok = await self._gate_ok(new_config)

            old_clients = self._clients
            old_paths = self._config.watch_paths if self._config else frozenset()
            new_paths = new_config.watch_paths
            roots = set(new_paths) if gate_ok else set()

            # --- atomic swap (NO await between these statements) -------------
            self._dispatcher.set_adapters(new_adapters)
            self._debouncer.update_servers(new_config.servers)
            self._config = new_config
            self._clients = new_clients
            self._watcher.set_roots(roots)
            self.state = EngineState.running if gate_ok else EngineState.blocked
            # ----------------------------------------------------------------

            log.info(
                "engine.rebuilt",
                gate_ok=gate_ok,
                state=self.state.value,
                added=sorted(new_paths - old_paths) if gate_ok else [],
                removed=sorted(old_paths - new_paths) if gate_ok else sorted(old_paths),
                servers=len(new_config.servers),
                routes=len(new_config.routes),
            )

            # Safe to await now: new requests already use new adapters/clients.
            for client in old_clients.values():
                await client.aclose()
```

> Note: the old `start` body inlined the gate check and `return`ed before building adapters; the new
> body builds the pipeline first (no I/O) then gates. Creating an `InotifyBackend` allocates an
> inotify fd but **no watches** (watches are consumed only by `set_roots`), so building it before a
> blocked early-return is safe and is torn down by `aclose()`.

- [ ] **Step 4: Run the engine suite — expect PASS**

```bash
pytest tests/test_engine.py -q
```

Expected: PASS — the updated existing test, the four new transition tests, and every untouched test
(`test_rebuild_adds_then_removes_roots_and_reroutes`, `test_gate_off_attaches_despite_insufficient_limit`,
`test_aclose_*`, etc.). The autouse `_gate_ok` fixture's `check_watch_limit` → `ok=True` keeps the
gate-ok paths identical to before.

- [ ] **Step 5: Lint + type-check**

```bash
ruff check mediascanmonitor/engine.py tests/test_engine.py && mypy mediascanmonitor/engine.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/engine.py tests/test_engine.py
git commit -m "feat(engine): blocked<->running gate-recovery via zero-root watcher swap (no restart)"
```

---

### Task 3: Headless `serve_headless` passes `park_when_blocked=False` (§J)

Wire the headless path to the new keyword and confirm the exit-3-on-blocked contract holds under §I.

**Files:**
- Modify: `mediascanmonitor/cli.py`
- Test: `tests/test_cli.py` (add a headless-blocked test; the existing
  `test_serve_headless_shuts_down_on_stop_event` keeps passing unchanged)

**Interfaces:**
- Consumes: `Engine.start(park_when_blocked=False)`; `EngineState.blocked`.
- Produces: `serve_headless` unchanged signature; exit code `3` when blocked, `0` clean.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (extend the existing imports from `tests._helpers` to add
`make_route` and `make_server_runtime`, and add `from mediascanmonitor.watcher.watch_limit import
WatchLimitStatus` plus `from mediascanmonitor.engine import EngineState`):

```python
async def test_serve_headless_blocked_returns_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    server = make_server_runtime(1, name="plex")
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        cli_module.engine_module,
        "build_runtime_config",
        lambda repo: make_config([route], [server]),
    )
    monkeypatch.setattr(
        cli_module.engine_module,
        "create_adapter",
        lambda server, client: RecordingAdapter(server, client),
    )
    monkeypatch.setattr(cli_module.engine_module, "build_client", lambda **_: FakeClient())
    monkeypatch.setattr(
        cli_module.engine_module,
        "check_watch_limit",
        lambda paths, ignore: WatchLimitStatus(
            current=10, dirs=100, needed=120, recommended=144, ok=False
        ),
    )

    class _StubRepo:
        def get_setting(self, key: str) -> str | None:
            return "enforce"

    watcher = FakeWatcher()
    code = await serve_headless(
        cast(Repo, _StubRepo()), watcher=watcher, install_signals=False
    )

    assert code == 3
    assert watcher.roots_history == []  # blocked before set_roots (headless contract)
```

- [ ] **Step 2: Run it — expect FAIL**

```bash
pytest tests/test_cli.py::test_serve_headless_blocked_returns_exit_3 -q
```

Expected: FAIL — under today's default (`park_when_blocked=True`) the engine parks at zero roots and
the start task never completes, so `serve_headless` does not see `blocked` from the start task and the
test would hang/return `0`. (If it appears to hang, that itself confirms the missing change.)

- [ ] **Step 3: Minimal implementation**

In `mediascanmonitor/cli.py`, change the `serve_headless` start-task line:

```python
    start_task = asyncio.create_task(engine.start())
```

to:

```python
    start_task = asyncio.create_task(engine.start(park_when_blocked=False))
```

- [ ] **Step 4: Run it — expect PASS**

```bash
pytest tests/test_cli.py -q
```

Expected: PASS — the new blocked test plus `test_serve_headless_shuts_down_on_stop_event` (empty
config → gate ok → running → stop event → clean exit 0).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/cli.py tests/test_cli.py
git commit -m "fix(cli): headless engine parks=False so a blocked gate exits 3 under new gate logic"
```

---

### Task 4: System & status surface — `web/api/system.py` (§H)

`StatusRead` + `/health` (unauth, always 200), `/ready` (auth; 200 iff DB reachable and engine
`running`), `/api/status`, `PUT /api/settings/inotify-gate`, `POST /api/engine/recheck`. Mount in
`create_app`.

**Files:**
- Create: `mediascanmonitor/web/api/system.py`
- Modify: `mediascanmonitor/web/app.py` (include the two routers — the one shared merge point)
- Test: `tests/web/test_system.py`

**Interfaces:**
- Consumes: `get_repo`, `get_engine`, `require_api_auth` (`web/deps.py`); `Repo`
  (`db/repo.py`); `Engine`, `EngineState` (`engine.py`); `rebuild_engine` (`web/rebuild.py`, §F);
  `asyncio` (off-thread repo calls).
- Produces: `StatusRead` (Pydantic), `health_router` (unauth, `GET /health`), `router` (guarded:
  `/ready`, `/api/status`, `PUT /api/settings/inotify-gate`, `POST /api/engine/recheck`).

> **Test harness note:** these route tests use the sub-plan-01 harness FakeEngine (settable
> `.state`/`.watch_limit`, call-counting async `rebuild()`). The FakeEngine's state does not change
> on its own — we set it directly to assert `/ready` and `/api/status` behavior. Real
> `blocked↔running` transitions are covered in Task 2 against a real `Engine`. `/health` is reached
> via the **unauth** `client`; everything else via `auth_client`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_system.py`:

```python
"""System & status routes (contract §H): liveness, readiness, status, gate control."""

from fastapi.testclient import TestClient

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate
from mediascanmonitor.engine import EngineState
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus


def test_health_is_unauthenticated_and_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_requires_auth(client: TestClient) -> None:
    assert client.get("/ready").status_code == 401


def test_ready_200_when_running(auth_client: TestClient, engine: object) -> None:
    engine.state = EngineState.running  # type: ignore[attr-defined]
    assert auth_client.get("/ready").status_code == 200


def test_ready_503_when_blocked(auth_client: TestClient, engine: object) -> None:
    engine.state = EngineState.blocked  # type: ignore[attr-defined]
    resp = auth_client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "blocked"


def test_status_reports_state_counts_and_watch_limit(
    auth_client: TestClient, repo: Repo, engine: object
) -> None:
    repo.create_server(ServerCreate(name="a", type=ServerType.webhook, base_url="https://h/x"))
    repo.create_server(
        ServerCreate(name="b", type=ServerType.webhook, base_url="https://h/y", enabled=False)
    )
    engine.state = EngineState.running  # type: ignore[attr-defined]
    engine.watch_limit = WatchLimitStatus(  # type: ignore[attr-defined]
        current=100, dirs=40, needed=48, recommended=58, ok=True
    )

    body = auth_client.get("/api/status").json()
    assert body["engine_state"] == "running"
    assert body["inotify_gate"] == "enforce"  # default
    assert body["server_count"] == 2
    assert body["enabled_server_count"] == 1
    assert body["watch_current"] == 100
    assert body["watch_needed"] == 48
    assert body["watch_ok"] is True


def test_status_watch_fields_none_when_unevaluated(
    auth_client: TestClient, engine: object
) -> None:
    engine.watch_limit = None  # type: ignore[attr-defined]
    body = auth_client.get("/api/status").json()
    assert body["watch_current"] is None
    assert body["watch_ok"] is None


def test_put_inotify_gate_sets_setting_and_rebuilds(
    auth_client: TestClient, repo: Repo, engine: object
) -> None:
    resp = auth_client.put("/api/settings/inotify-gate", json={"inotify_gate": "off"})
    assert resp.status_code == 200
    assert resp.json()["inotify_gate"] == "off"
    assert repo.get_setting("inotify_gate") == "off"
    assert engine.rebuild_calls >= 1  # type: ignore[attr-defined]


def test_put_inotify_gate_rejects_unknown_value(auth_client: TestClient) -> None:
    assert auth_client.put(
        "/api/settings/inotify-gate", json={"inotify_gate": "maybe"}
    ).status_code == 422


def test_recheck_rebuilds_and_returns_status(
    auth_client: TestClient, engine: object
) -> None:
    before = engine.rebuild_calls  # type: ignore[attr-defined]
    resp = auth_client.post("/api/engine/recheck")
    assert resp.status_code == 200
    assert "engine_state" in resp.json()
    assert engine.rebuild_calls == before + 1  # type: ignore[attr-defined]
```

> If the sub-plan-01 FakeEngine exposes its call count under a different name than `rebuild_calls`,
> adjust these two assertions to that name — the harness is the source of truth. The FakeEngine must
> also start with `.watch_limit` set or `None`; the status tests set it explicitly.

- [ ] **Step 2: Run them — expect FAIL**

```bash
pytest tests/web/test_system.py -q
```

Expected: FAIL — `web/api/system.py` does not exist and the routers are not mounted (404s / import
error).

- [ ] **Step 3: Implement `web/api/system.py`**

Create `mediascanmonitor/web/api/system.py`:

```python
"""System & status surface (contract §H).

Liveness (`/health`, unauth — the UI stays reachable even when the engine is blocked),
readiness (`/ready`, auth — 200 iff DB reachable AND engine running), status
(`/api/status`), and the user-facing gate controls (`PUT /api/settings/inotify-gate`,
`POST /api/engine/recheck`). All repo work runs off the loop via ``asyncio.to_thread``.
No secret is ever read or returned here (invariant 1).
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine, EngineState
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.rebuild import rebuild_engine


class StatusRead(BaseModel):
    engine_state: str  # EngineState value: starting|running|blocked|stopped
    inotify_gate: str  # "enforce" | "off"
    watch_current: int | None
    watch_dirs: int | None
    watch_needed: int | None
    watch_recommended: int | None
    watch_ok: bool | None
    server_count: int
    enabled_server_count: int


class InotifyGateUpdate(BaseModel):
    inotify_gate: Literal["enforce", "off"]


async def build_status(repo: Repo, engine: Engine) -> StatusRead:
    gate = await asyncio.to_thread(repo.get_setting, "inotify_gate")
    all_servers = await asyncio.to_thread(repo.list_servers)
    enabled_servers = await asyncio.to_thread(repo.list_servers, enabled_only=True)
    wl = engine.watch_limit
    return StatusRead(
        engine_state=engine.state.value,
        inotify_gate=gate or "enforce",
        watch_current=wl.current if wl else None,
        watch_dirs=wl.dirs if wl else None,
        watch_needed=wl.needed if wl else None,
        watch_recommended=wl.recommended if wl else None,
        watch_ok=wl.ok if wl else None,
        server_count=len(all_servers),
        enabled_server_count=len(enabled_servers),
    )


# --- liveness: unauthenticated (allow-list, contract §B) --------------------
health_router = APIRouter()


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- readiness/status/controls: authenticated -------------------------------
router = APIRouter(dependencies=[Depends(require_api_auth)])


@router.get("/ready")
async def ready(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> JSONResponse:
    try:
        await asyncio.to_thread(repo.get_setting, "inotify_gate")  # DB reachability probe
    except Exception:
        return JSONResponse({"status": "db unreachable"}, status_code=503)
    if engine.state is EngineState.running:
        return JSONResponse({"status": "ready"}, status_code=200)
    return JSONResponse({"status": engine.state.value}, status_code=503)


@router.get("/api/status", response_model=StatusRead)
async def api_status(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    return await build_status(repo, engine)


@router.put("/api/settings/inotify-gate", response_model=StatusRead)
async def set_inotify_gate(
    body: InotifyGateUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    await asyncio.to_thread(repo.set_setting, "inotify_gate", body.inotify_gate)
    await rebuild_engine(engine)  # flipping to "off" recovers a blocked engine (§I blocked->running)
    return await build_status(repo, engine)


@router.post("/api/engine/recheck", response_model=StatusRead)
async def engine_recheck(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    await rebuild_engine(engine)  # re-evaluate the gate after an out-of-band kernel-limit change
    return await build_status(repo, engine)
```

> `/ready`'s single condition (DB reachable AND engine running) **subsumes** PLAN's three: the engine
> only reaches `running` when the watcher is attached and the gate passed (§I). `engine` is the
> FakeEngine in tests / the real `Engine` in production — both expose `.state` and `.watch_limit`.

- [ ] **Step 4: Mount the routers in `create_app`**

Modify `mediascanmonitor/web/app.py` — alongside the other `include_router(...)` lines in
`create_app` (the shared merge point: keep every existing `include_router` line), add the system
import and the two includes:

```python
from mediascanmonitor.web.api import system as system_api
```

and, inside `create_app` where the routers are mounted:

```python
    app.include_router(system_api.health_router)
    app.include_router(system_api.router)
```

- [ ] **Step 5: Run the system tests — expect PASS**

```bash
pytest tests/web/test_system.py -q
```

Expected: PASS — `/health` 200 unauth; `/ready` 401 unauth, 200 running / 503 blocked; `/api/status`
counts + watch fields; gate `PUT` sets the setting + rebuilds (422 on a bad value); `recheck`
rebuilds.

- [ ] **Step 6: Lint + type-check**

```bash
ruff check mediascanmonitor/web/api/system.py mediascanmonitor/web/app.py tests/web/test_system.py && mypy mediascanmonitor/web/api/system.py mediascanmonitor/web/app.py
```

Expected: clean. (Note: `except Exception:` needs **no** `# noqa` — `BLE` is not in the ruff select.)

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/api/system.py mediascanmonitor/web/app.py tests/web/test_system.py
git commit -m "feat(web): system surface — /health /ready /api/status + inotify-gate recovery routes"
```

---

### Task 5: `run` web wiring — `web/server.py` + `cli.py` (§J)

`serve_web` runs uvicorn + `engine.start()` concurrently on one loop. `cli._cmd_run` without
`--no-web` now serves; `_build_repo` gains a `_load_key` helper so the run path can reuse the Fernet
key (decoded) as the session secret.

**Files:**
- Create: `mediascanmonitor/web/server.py`
- Modify: `mediascanmonitor/cli.py`
- Test: `tests/web/test_server.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `EventsBus` (`observ/events_bus.py`); `Engine` (`engine.py`); `create_app`
  (`web/app.py`); `bootstrap_password` (`web/auth.py`); `uvicorn.Config`/`uvicorn.Server`;
  `Repo` (`db/repo.py`).
- Produces:
  ```python
  async def serve_web(
      repo: Repo,
      *,
      host: str = "0.0.0.0",
      port: int = 8080,
      session_secret: str,
      stop_event: asyncio.Event | None = None,
  ) -> int: ...
  def _load_key() -> bytes: ...   # cli.py helper; _build_repo reuses it
  ```

> **serve_web testing decision (no real socket bind):** the lifecycle test monkeypatches
> `uvicorn.Server.serve` with an `async` stub that simply awaits the `stop_event`, and monkeypatches
> `web.server.Engine` with a fake whose `start()` blocks until cancelled and whose `aclose()` sets a
> flag. We then drive `serve_web` with a pre-set `stop_event` and assert it returns `0` and that the
> engine's `aclose()` ran. No port is opened, no uvicorn server starts. (Hitting `/health` on an
> ephemeral port is the alternative; we avoid it to keep the test hermetic and fast.)

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_server.py`:

```python
"""serve_web lifecycle — uvicorn + engine on one loop, stopped via stop_event (no real bind)."""

import asyncio

import pytest
import uvicorn

from mediascanmonitor import web as _web_pkg  # noqa: F401  (ensure package import path)
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import EngineState
from mediascanmonitor.web import server as server_module


async def test_serve_web_starts_engine_and_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch, repo: Repo
) -> None:
    created: dict[str, "_FakeEngine"] = {}

    class _FakeEngine:
        def __init__(self, repo: Repo, *, events_bus: object | None = None) -> None:
            self.closed = False
            self.started = False
            self.state = EngineState.running
            self.watch_limit = None

        async def start(self, *, park_when_blocked: bool = True) -> None:
            self.started = True
            await asyncio.Event().wait()  # block until the task is cancelled at shutdown

        async def aclose(self) -> None:
            self.closed = True

    def make_engine(repo: Repo, *, events_bus: object | None = None) -> _FakeEngine:
        eng = _FakeEngine(repo, events_bus=events_bus)
        created["engine"] = eng
        return eng

    monkeypatch.setattr(server_module, "Engine", make_engine)
    monkeypatch.setattr(server_module, "bootstrap_password", lambda repo: None)

    stop = asyncio.Event()

    async def fake_serve(self: uvicorn.Server) -> None:
        await stop.wait()  # stand in for "serve until shutdown"; never binds a socket

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)

    stop.set()  # request shutdown immediately
    code = await server_module.serve_web(repo, session_secret="x" * 32, stop_event=stop)

    assert code == 0
    assert created["engine"].started is True
    assert created["engine"].closed is True  # engine.aclose() ran on shutdown
```

Append to `tests/test_cli.py` (replace the obsolete exit-2 stub test
`test_run_without_no_web_prints_phase3_message` with this serves test; add the new
headless-blocked test from Task 3 if not already present):

```python
def test_run_without_no_web_invokes_serve_web(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSM_HOST", raising=False)
    monkeypatch.delenv("MSM_PORT", raising=False)
    monkeypatch.setattr(cli_module, "_build_repo", lambda: cast(Repo, object()))
    monkeypatch.setattr(cli_module, "_load_key", lambda: b"k" * 44)
    monkeypatch.setattr(cli_module, "configure_logging", lambda **_: None)

    captured: dict[str, object] = {}

    async def fake_serve_web(
        repo: Repo,
        *,
        host: str,
        port: int,
        session_secret: str,
        stop_event: object | None = None,
    ) -> int:
        captured["host"] = host
        captured["port"] = port
        captured["session_secret"] = session_secret
        return 0

    monkeypatch.setattr(cli_module, "serve_web", fake_serve_web)

    assert main(["run"]) == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080
    assert captured["session_secret"] == ("k" * 44)  # Fernet key decoded to str
```

Delete the old `test_run_without_no_web_prints_phase3_message` test body.

- [ ] **Step 2: Run them — expect FAIL**

```bash
pytest tests/web/test_server.py tests/test_cli.py -q
```

Expected: FAIL — `mediascanmonitor.web.server` does not exist; `cli` has no `_load_key`/`serve_web`
and `_cmd_run` still prints the Phase-3 stub + returns 2.

- [ ] **Step 3: Implement `web/server.py`**

Create `mediascanmonitor/web/server.py`:

```python
"""Serve the web dashboard and the engine on one asyncio event loop (contract §J).

``serve_web`` builds the events bus, the engine (with the bus wired), bootstraps the
first-run password, builds the FastAPI app, and runs ``uvicorn.Server.serve`` and
``engine.start()`` concurrently. The engine *parks* if the inotify gate is blocked
(``park_when_blocked=True`` default) so the web layer always serves (invariant 5).
Shutdown (SIGINT/SIGTERM handled by uvicorn, or ``stop_event``) closes the engine and
cancels the start task. Returns process exit code ``0``.
"""

import asyncio
import contextlib

import structlog
import uvicorn

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.web.app import create_app
from mediascanmonitor.web.auth import bootstrap_password

log = structlog.get_logger("web.server")


async def serve_web(
    repo: Repo,
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    session_secret: str,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop = stop_event if stop_event is not None else asyncio.Event()

    bus = EventsBus()
    engine = Engine(repo, events_bus=bus)
    await asyncio.to_thread(bootstrap_password, repo)  # never logs the value
    app = create_app(repo, engine, bus, session_secret=session_secret)

    config = uvicorn.Config(app, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)

    start_task = asyncio.create_task(engine.start())  # parks if the gate is blocked
    serve_task = asyncio.create_task(server.serve())
    stop_task = asyncio.create_task(stop.wait())
    log.info("web.serving", host=host, port=port)
    try:
        await asyncio.wait(
            {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        server.should_exit = True  # ask uvicorn to wind down its accept loop
        await engine.aclose()  # closes watcher -> events() ends -> start_task returns
        start_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await start_task
        with contextlib.suppress(asyncio.CancelledError):
            await serve_task
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
    log.info("web.stopped")
    return 0
```

- [ ] **Step 4: Refactor `cli.py` — `_load_key` helper + web `run` path**

In `mediascanmonitor/cli.py`:

Add the `serve_web` import to the first-party block:

```python
from mediascanmonitor.web.server import serve_web
```

Add it to `__all__`:

```python
__all__ = ["build_parser", "engine_module", "main", "serve_headless", "serve_web"]
```

Factor `_load_key` and reuse it in `_build_repo` (keeps `_build_repo() -> Repo` so the existing
headless CLI/e2e tests that monkeypatch or call it are unaffected):

```python
def _load_key() -> bytes:
    """Resolve the Fernet secret key (env value > file > generate). Returns the urlsafe-base64 key."""
    key_path = Path(os.environ.get("MSM_SECRET_KEY_FILE", _DEFAULT_KEY_PATH))
    env_key = os.environ.get("MSM_SECRET_KEY")
    return load_or_create_key(key_path, env_key=env_key)


def _build_repo() -> Repo:
    """Assemble the repository from env/Docker config. Raises on misconfiguration."""
    db_path = Path(os.environ.get("MSM_DB_PATH", _DEFAULT_DB_PATH))
    box = SecretBox(_load_key())
    engine = init_db(db_path)  # returns the Engine (contract §4); not a factory
    return Repo(session_factory(engine), box)
```

Replace `_cmd_run` (remove the Phase-1 stub + exit-2; serve when `--no-web` is absent). The session
secret reuses the Fernet key — `load_or_create_key` returns **bytes**, but `create_app`/Starlette's
`SessionMiddleware.secret_key` want **str**, so `.decode("ascii")` the urlsafe-base64 key:

```python
def _cmd_run(args: argparse.Namespace) -> int:
    try:
        repo = _build_repo()
        session_secret = "" if args.no_web else _load_key().decode("ascii")
    except Exception as exc:  # fail fast with a clear message, not a traceback
        print(f"startup error: {exc}", file=sys.stderr)
        return 1

    configure_logging()
    if args.no_web:
        return asyncio.run(serve_headless(repo))  # 0 clean, 3 if the inotify gate blocked startup

    host = os.environ.get("MSM_HOST", "0.0.0.0")
    port = int(os.environ.get("MSM_PORT", "8080"))
    return asyncio.run(
        serve_web(repo, host=host, port=port, session_secret=session_secret)
    )
```

Update the module docstring's first paragraph to drop the "web arrives in Phase 3 / exits non-zero"
note (it now serves).

- [ ] **Step 5: Run the suites — expect PASS**

```bash
pytest tests/web/test_server.py tests/test_cli.py -q
```

Expected: PASS — `serve_web` returns 0 and closes the engine; `run` (no `--no-web`) calls `serve_web`
with `0.0.0.0:8080` and the decoded key; `run --no-web` still calls `serve_headless`; startup-failure
still returns 1.

- [ ] **Step 6: Lint + type-check**

```bash
ruff check mediascanmonitor/web/server.py mediascanmonitor/cli.py tests/web/test_server.py tests/test_cli.py && mypy mediascanmonitor/web/server.py mediascanmonitor/cli.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/server.py mediascanmonitor/cli.py tests/web/test_server.py tests/test_cli.py
git commit -m "feat(cli,web): serve uvicorn+engine on one loop for \`run\`; reuse Fernet key as session secret"
```

---

### Task 6: Followups + full-suite verification gate

**Files:**
- Modify: `docs/superpowers/plans/2026-06-20-phase3-README.md` (strike the gate-recovery followup),
  `docs/FOLLOWUPS.md` (remove the now-done `rebuild()` gate-recovery item).

- [ ] **Step 1: Mark the gate-recovery followup done**

In `docs/FOLLOWUPS.md`, remove (or check off) the **`rebuild()` blocked↔running gate-recovery** item
now that §I landed. In the phase3 README's "After Phase 3" note, strike the same item.

- [ ] **Step 2: Run the full gate**

```bash
ruff check . && ruff format --check . && mypy mediascanmonitor && pytest
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add docs/FOLLOWUPS.md docs/superpowers/plans/2026-06-20-phase3-README.md
git commit -m "docs(followups): mark rebuild() blocked<->running gate-recovery done (phase3-03)"
```

---

## Verification

The sub-plan is complete when, after `export PATH="$PWD/.venv/bin:$PATH"`:

```bash
ruff check . && ruff format --check . && mypy mediascanmonitor && pytest
```

is green, and specifically:

- **§G** — `Engine(repo, events_bus=bus)` publishes a redacted `EventRecord` (no secret) per
  dispatch; `events_bus=None` reproduces Phase 1/2 dispatch exactly (every pre-existing engine test
  still passes).
- **§I** — `blocked→running` (×2: policy-off and limit-raise) and `running→blocked` pass against a
  real `Engine` + `FakeWatcher` + monkeypatched `check_watch_limit`; `running→running` stays covered
  by the existing rebuild root-swap test and `blocked→blocked` by the shared `_gate_ok`-False branch
  (so every rebuild branch is exercised); the headless `park_when_blocked=False` path returns
  `blocked` with `roots_history == []`; the one updated existing test
  (`test_blocked_when_watch_limit_insufficient_and_enforced`) passes.
- **§H** — `/health` is 200 unauth even when blocked; `/ready` is 200 only when `running`, else 503;
  `/api/status` reports state + watch limit + server counts; `PUT /api/settings/inotify-gate` sets
  the setting and rebuilds; `POST /api/engine/recheck` rebuilds; bad gate value → 422.
- **§J** — `serve_web` runs uvicorn + engine on one loop, stops on `stop_event`, closes the engine,
  returns 0 (tested with `uvicorn.Server.serve` monkeypatched — no real socket bind); `run` without
  `--no-web` serves; `run --no-web` still exits 0/3; the Fernet key is decoded and reused as the
  session secret.
- Invariant 5 holds: the gate never wedges the web layer — the engine parks at zero roots while the
  app keeps serving.
