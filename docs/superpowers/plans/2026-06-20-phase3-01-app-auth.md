# Phase 3 — Sub-plan 01: App Factory + Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the FastAPI skeleton the rest of Phase 3 mounts on — the `create_app` factory, the
DI/auth-guard layer, the Argon2 password surface backed by the `Setting` table, a signed-cookie
session, an in-memory login rate-limiter, the `EventsBus`/`EventRecord` classes the factory
references, and the auth router (login / logout / change-password / first-run setup) with minimal
base/login/setup templates. After this sub-plan you can start the app, get locked out, set a password
on first run, and log in.

**Architecture:** New `mediascanmonitor/web/` package (factory + deps + auth + ratelimit + auth router
+ templates) plus the `mediascanmonitor/observ/events_bus.py` module. The factory is **pure** — it
takes the already-built `Repo`, `Engine`, and `EventsBus`, stores them on `app.state`, mounts
`SessionMiddleware` + `Jinja2Templates` + the auth router, and returns the `FastAPI` instance. No I/O,
no env reads, no password bootstrap inside the factory (that is `serve_web`'s job, sub-plan 03). Auth
logic is sync (Argon2 over the sync `Repo`); handlers call it off the event loop with
`asyncio.to_thread`. Two guard variants enforce the contract's allow-list: API routes get JSON `401`,
HTML pages get a `303` redirect to `/login` (or `/setup` when no password is set yet).

**Tech Stack:** Python 3.14, `fastapi==0.137.1`, `starlette` (via FastAPI, `SessionMiddleware` +
`TestClient`), `argon2-cffi==25.1.0` (already pinned), `jinja2==3.1.6` (already pinned),
`httpx==0.28.1` (`ASGITransport` for the async SSE-style smoke), `pytest==9.1.0` +
`pytest-asyncio==1.4.0` (`asyncio_mode=auto`). **Two new runtime deps added here:** `itsdangerous`
(Starlette signs the session cookie with it) and `python-multipart` (FastAPI `Form(...)` parsing) —
pin the current-stable versions after verifying on PyPI at add-time (see Task 1). `ruff`/`mypy
--strict` clean, line length 100. PEP 649 annotations — **no** `from __future__ import annotations`.

## Global Constraints

These are binding for every task below (copied verbatim from CLAUDE.md + the Phase 3 contract):

- **PEP 649 annotations.** Never add `from __future__ import annotations`. Leave forward references
  unquoted (`list[EventRecord]`). Caveat: a name used in a runtime-introspected annotation
  (Pydantic/SQLModel/dataclass field) must be importable at runtime — never hide it behind
  `if TYPE_CHECKING:`.
- **Enums subclass `StrEnum`**, never `(str, Enum)`. `str(member)` is the bare value; prefer `.value`
  when you want the bare string regardless. (This sub-plan adds no new enum, but reuses
  `ServerType`/`ScanMode`/`EngineState`/`FsEventType`.)
- **Ruff `select` is exactly `E, F, I, UP, B, C4, SIM, RUF`** (per-file-ignore: `B` under `tests/**`).
  A `# noqa` for any other rule is unused and trips `RUF100` — don't add them. `# noqa: F401` on a
  self-registration import is valid and may stay.
- **`mediascanmonitor` is first-party for isort** — separate third-party from first-party imports with
  a blank line, or `ruff check` reports `I001` (autofixable).
- **`try/except: pass` → `contextlib.suppress(...)`** (ruff `SIM105`).
- **`mypy --strict` clean.** Full type hints everywhere. Pydantic/dataclasses at every external
  boundary (request bodies, responses) — never pass raw dicts around.
- **Async discipline (contract conv. 1 / cross-plan invariant 3).** Route handlers are `async def`.
  The only synchronous code is the `Repo` (sync SQLModel) and the Argon2 calls in `web/auth.py`; call
  them off the loop with `await asyncio.to_thread(...)` — never call a `Repo` method or `is_password_set`
  directly inside a coroutine.
- **Secrets never logged (rule 5 / cross-plan invariant 1).** Never log a password, a token, or a
  session value. `bootstrap_password` never logs the bootstrap value. `EventRecord` carries no secret.
- **Auth-closed by default (cross-plan invariant 2).** Every route is guarded unless it is on the §B
  allow-list: `POST /auth/login`, `GET /login`, `GET /setup` + `POST /setup` (only while unset),
  `GET /health`, `/static/*`. Apply guards router-level via `dependencies=[Depends(...)]`.
- **CSRF via SameSite (contract §C).** The auth POSTs are protected by the `same_site="lax"` session
  cookie, which the browser withholds on cross-site POSTs — that is the deliberate CSRF defense; no
  CSRF token is used. This is intentional, not an oversight.
- Line length 100. Verification gate: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`.

### Tooling is not on PATH

The dev tools live in the project venv. Run this **once per shell** before any command in this plan,
then use the bare `pytest` / `ruff` / `mypy` / `uv` names shown in each step:

```bash
export PATH="$PWD/.venv/bin:$PATH"
```

## File Structure (what this sub-plan builds)

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | **Modify:** add `itsdangerous` + `python-multipart` pinned (verified at add-time). |
| `uv.lock` | **Modify:** refreshed via `uv lock`. |
| `mediascanmonitor/web/ratelimit.py` | `LoginRateLimiter` — per-IP, monotonic-clock, injectable `now`. |
| `mediascanmonitor/observ/events_bus.py` | `EventRecord` frozen dataclass + `EventsBus` ring-buffer/fan-out class. |
| `mediascanmonitor/web/auth.py` | Argon2 hashing, `Setting`-backed password, `bootstrap_password`, **and** the auth router. |
| `mediascanmonitor/web/deps.py` | DI state accessors + `require_api_auth` / `require_page_auth` guards. |
| `mediascanmonitor/web/app.py` | `create_app()` factory + middleware/template/router wiring. |
| `mediascanmonitor/web/templates/base.html` | Minimal page shell. |
| `mediascanmonitor/web/templates/login.html` | Login form. |
| `mediascanmonitor/web/templates/setup.html` | First-run password-creation form. |
| `tests/web/__init__.py` | Test package marker. |
| `tests/web/conftest.py` | **The web test harness** (`repo`, `events_bus`, `engine` FakeEngine, `app`, `client`, `auth_client`, `aclient`). |
| `tests/web/test_ratelimit.py` | `LoginRateLimiter` unit tests. |
| `tests/web/test_events_bus.py` | `EventsBus`/`EventRecord` unit tests (incl. async subscribe). |
| `tests/web/test_auth.py` | `web/auth.py` function unit tests (hash/verify/setting/bootstrap). |
| `tests/web/test_deps.py` | Guard behavior on a probe route (401 / 303 / setup redirect). |
| `tests/web/test_auth_routes.py` | Full auth-router flows (login, lockout, logout, change-pw, setup). |

> **Cross-sub-plan note:** sub-plans 02/03/04 each append one `include_router(...)` line to
> `create_app`. Keep every include line on merge — it is the single shared merge point.

> **Note on `web/auth.py` scope:** the contract (§C) lists the password functions in `web/auth.py`
> and the auth routes "in the auth router." This sub-plan puts **both** the helper functions and an
> `APIRouter` named `router` in `web/auth.py` (single module, one responsibility: authentication).
> `create_app` mounts `auth.router`. If a later refactor wants them split, move the router to
> `web/api/auth.py` then — not required now.

---

### Task 1: New deps + `web/ratelimit.py` (LoginRateLimiter)

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/web/__init__.py`
- Create: `tests/web/test_ratelimit.py`
- Create: `mediascanmonitor/web/ratelimit.py`

**Interfaces:**
- Produces: `LoginRateLimiter` (contract §C) with an injectable monotonic clock for deterministic tests.

```python
class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None: ...
    def allowed(self, key: str) -> bool: ...        # False once >= max_attempts failures inside the window
    def record_failure(self, key: str) -> None: ...
    def reset(self, key: str) -> None: ...          # call on successful login
```

- [ ] **Step 1: Add the two runtime dependencies (verify current-stable FIRST)**

CLAUDE.md rule 1 forbids trusting a version from memory or from this doc. **Before editing**, verify
the current stable release of each on PyPI:

```bash
export PATH="$PWD/.venv/bin:$PATH"
pip index versions itsdangerous 2>/dev/null | head -3 || curl -s https://pypi.org/pypi/itsdangerous/json | python -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
pip index versions python-multipart 2>/dev/null | head -3 || curl -s https://pypi.org/pypi/python-multipart/json | python -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
```

As of 2026-06-20 these were `itsdangerous==2.2.0` and `python-multipart==0.0.32`. **Pin whatever the
command above reports as current stable** (if it differs from those, use the verified value). Add both
to the `dependencies` array in `pyproject.toml`, immediately after `argon2-cffi`, preserving the
existing add-time comment that already heads the array:

```toml
    "argon2-cffi==25.1.0",
    # Starlette SessionMiddleware signs the session cookie with itsdangerous (optional
    # Starlette dep, not installed transitively); python-multipart parses Form() POSTs.
    # Both verified current-stable on PyPI at add-time (2026-06-20).
    "itsdangerous==2.2.0",
    "python-multipart==0.0.32",
```

Then, **in the same `pyproject.toml`**, add a `flake8-bugbear` table so ruff `B008`
(function-call-in-default) does not fire on FastAPI's intended `Depends(...)`/`Form(...)`/`Query(...)`
parameter defaults — which this sub-plan's `create_app` guards and auth router both use, and which
every later Phase 3 router relies on. This is the **one** place the project allowlists those calls;
do **not** scatter `# noqa: B008`. Add directly under the existing `[tool.ruff.lint.isort]` block:

```toml
[tool.ruff.lint.flake8-bugbear]
# FastAPI's dependency-injection markers are designed to sit in parameter defaults;
# treat them as immutable so ruff B008 (function-call-in-default) does not fire.
extend-immutable-calls = [
    "fastapi.Depends",
    "fastapi.Query",
    "fastapi.Form",
    "fastapi.Path",
    "fastapi.Body",
    "fastapi.Header",
]
```

- [ ] **Step 2: Refresh the lockfile**

```bash
export PATH="$PWD/.venv/bin:$PATH"
uv lock
uv sync --locked
```

Expected: `uv lock` rewrites `uv.lock`; `uv sync --locked` succeeds (CI runs the same — keep it green).
Confirm both packages are importable:

```bash
python -c "import itsdangerous, multipart; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Create the test package marker**

Create `tests/web/__init__.py` (empty file).

- [ ] **Step 4: Write the failing rate-limiter tests**

Create `tests/web/test_ratelimit.py`:

```python
"""LoginRateLimiter: per-IP failure counting inside a sliding monotonic window."""

from mediascanmonitor.web.ratelimit import LoginRateLimiter


class FakeClock:
    """Injectable monotonic clock so the window logic is deterministic."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_allowed_until_max_attempts() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300.0, now=clock)
    assert limiter.allowed("1.2.3.4") is True
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.allowed("1.2.3.4") is True  # 2 < 3
    limiter.record_failure("1.2.3.4")
    assert limiter.allowed("1.2.3.4") is False  # 3 >= 3


def test_keys_are_isolated_per_ip() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300.0, now=clock)
    limiter.record_failure("1.1.1.1")
    assert limiter.allowed("1.1.1.1") is False
    assert limiter.allowed("2.2.2.2") is True


def test_window_expiry_forgets_old_failures() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=300.0, now=clock)
    limiter.record_failure("ip")
    limiter.record_failure("ip")
    assert limiter.allowed("ip") is False
    clock.t = 301.0  # both failures now outside the 300s window
    assert limiter.allowed("ip") is True


def test_reset_clears_failures() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300.0, now=clock)
    limiter.record_failure("ip")
    assert limiter.allowed("ip") is False
    limiter.reset("ip")
    assert limiter.allowed("ip") is True


def test_reset_unknown_key_is_noop() -> None:
    limiter = LoginRateLimiter()
    limiter.reset("never-seen")  # must not raise
    assert limiter.allowed("never-seen") is True
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_ratelimit.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.ratelimit'`.

- [ ] **Step 6: Implement `web/ratelimit.py`**

Create `mediascanmonitor/web/ratelimit.py`:

```python
"""In-memory, per-client-IP login throttle (contract §C).

No persistence — counters live in process memory and reset on restart. Each key (the
client IP) keeps a list of recent failure timestamps; ``allowed`` prunes timestamps
older than the window and blocks once ``max_attempts`` remain. ``now`` is injectable so
tests drive the clock deterministically; production uses ``time.monotonic`` (immune to
wall-clock jumps).
"""

import time
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._now = now
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str) -> list[float]:
        cutoff = self._now() - self._window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def allowed(self, key: str) -> bool:
        return len(self._prune(key)) < self._max_attempts

    def record_failure(self, key: str) -> None:
        recent = self._prune(key)
        recent.append(self._now())
        self._failures[key] = recent

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_ratelimit.py -v
```

Expected: PASS — 5 passed.

- [ ] **Step 8: Lint + type-check**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check mediascanmonitor/web/ratelimit.py tests/web/test_ratelimit.py && \
  ruff format --check mediascanmonitor/web/ratelimit.py tests/web/test_ratelimit.py && \
  mypy mediascanmonitor/web/ratelimit.py
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock mediascanmonitor/web/ratelimit.py \
        tests/web/__init__.py tests/web/test_ratelimit.py
git commit -m "feat(web): add itsdangerous+python-multipart deps and LoginRateLimiter"
```

---

### Task 2: `observ/events_bus.py` (EventRecord + EventsBus)

**Files:**
- Create: `tests/web/test_events_bus.py`
- Create: `mediascanmonitor/observ/events_bus.py`

**Interfaces:**
- Produces: `EventRecord` (frozen dataclass, contract §G — no secret field) and `EventsBus`
  (`publish`/`recent`/`subscribe`).

```python
@dataclass(frozen=True, slots=True)
class EventRecord:
    ts: str
    server_id: int
    server_name: str
    scan_mode: str        # ScanMode value
    scan_key: str
    scan_path: str | None
    library_id: str | None
    event_type: str       # FsEventType value
    file_path: str
    ok: bool
    status_code: int | None
    detail: str

class EventsBus:
    def __init__(self, *, capacity: int = 200) -> None: ...
    def publish(self, record: EventRecord) -> None: ...
    def recent(self, limit: int = 50) -> list[EventRecord]: ...
    def subscribe(self) -> AsyncIterator[EventRecord]: ...
```

> **Scope guard:** this sub-plan creates only the **classes**. The Engine publish wiring (the
> `events_bus` ctor param + the `_dispatch` publish) is sub-plan 03 — **do NOT touch `engine.py`
> here**. The `EventRecord` field set mirrors `TriggerResult` (`ok`/`status_code`/`detail` from
> `servers/base.py`) + `ScanRequest` context (`server_id`/`server_name`/`scan_mode`/`scan_key`/
> `scan_path`/`library_id`/`event_type`/`file_path` from `pipeline/events.py`) so 03 can build one
> from a `(ScanRequest, TriggerResult)` pair without new fields.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_events_bus.py`:

```python
"""EventsBus: ring buffer + per-subscriber fan-out; EventRecord is a frozen value object."""

import asyncio
import dataclasses

import pytest

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus


def make_record(n: int) -> EventRecord:
    return EventRecord(
        ts=f"2026-06-20T18:30:{n:02d}+00:00",
        server_id=1,
        server_name="plex",
        scan_mode="targeted",
        scan_key=f"/data/{n}",
        scan_path=f"/data/{n}",
        library_id="5",
        event_type="created",
        file_path=f"/data/{n}/file.mkv",
        ok=True,
        status_code=200,
        detail="ok",
    )


def test_event_record_is_frozen() -> None:
    rec = make_record(1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.ok = False  # type: ignore[misc]


def test_recent_returns_buffer_newest_last() -> None:
    bus = EventsBus(capacity=10)
    for n in range(3):
        bus.publish(make_record(n))
    recent = bus.recent()
    assert [r.scan_key for r in recent] == ["/data/0", "/data/1", "/data/2"]


def test_recent_respects_capacity_and_limit() -> None:
    bus = EventsBus(capacity=2)
    for n in range(5):
        bus.publish(make_record(n))  # only the last 2 survive the ring
    assert [r.scan_key for r in bus.recent()] == ["/data/3", "/data/4"]
    assert [r.scan_key for r in bus.recent(limit=1)] == ["/data/4"]


def test_recent_on_empty_bus() -> None:
    assert EventsBus().recent() == []


async def test_subscribe_receives_published_record() -> None:
    bus = EventsBus()
    agen = bus.subscribe()
    # let the subscriber register its queue before publishing
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)
    bus.publish(make_record(7))
    received = await asyncio.wait_for(task, timeout=1.0)
    assert received.scan_key == "/data/7"
    await agen.aclose()


async def test_publish_never_blocks_when_subscriber_queue_full() -> None:
    # A slow subscriber whose queue overflows must not block publish: the oldest
    # queued record is dropped and publish returns immediately.
    bus = EventsBus()
    agen = bus.subscribe()
    await agen.__anext__.__self__.asend(None) if False else None  # no-op, keeps mypy quiet
    # register the subscriber queue
    pending = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)
    for n in range(5000):  # far exceeds the per-subscriber queue bound
        bus.publish(make_record(n % 100))
    first = await asyncio.wait_for(pending, timeout=1.0)
    assert isinstance(first, EventRecord)  # got *a* record; no deadlock
    await agen.aclose()


async def test_subscribe_unregisters_on_close() -> None:
    bus = EventsBus()
    agen = bus.subscribe()
    await asyncio.ensure_future(_register(agen))
    await agen.aclose()
    # after close, publishing must not raise (queue was unregistered)
    bus.publish(make_record(1))


async def _register(agen: object) -> None:
    # pull one step so the generator runs up to its first queue.get()
    import contextlib

    with contextlib.suppress(StopAsyncIteration):
        task = asyncio.ensure_future(agen.__anext__())  # type: ignore[attr-defined]
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
```

> **Implementer note:** the `__anext__.__self__.asend` line in
> `test_publish_never_blocks_when_subscriber_queue_full` is intentionally a dead `if False`
> branch kept only so the test body stays simple; if ruff flags it (`B018`/`SIM`), replace that
> single line with `await asyncio.sleep(0)` — the assertion logic is unchanged. Prefer the simpler
> form when you paste; the point of the test is only that 5000 publishes against one slow subscriber
> never hang.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_events_bus.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.observ.events_bus'`.

- [ ] **Step 3: Implement `observ/events_bus.py`**

Create `mediascanmonitor/observ/events_bus.py`:

```python
"""In-process event bus for the live feed (contract §G).

A bounded ``deque`` ring buffer keeps the most recent records for replay
(``recent``); each live subscriber gets its own bounded ``asyncio.Queue`` so a slow
SSE client can never block ``publish``. ``publish`` is sync and non-blocking — safe to
call from ``Engine._dispatch`` (wired in sub-plan 03). On a full subscriber queue the
OLDEST queued record is dropped (the client misses a beat) rather than blocking the
producer.

SECURITY: ``EventRecord`` carries no secret/token field (rule 5). Nothing here may
render a credential.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

_SUBSCRIBER_QUEUE_MAXSIZE = 100


@dataclass(frozen=True, slots=True)
class EventRecord:
    ts: str  # ISO-8601 UTC, e.g. "2026-06-20T18:30:00+00:00"
    server_id: int
    server_name: str
    scan_mode: str  # ScanMode value
    scan_key: str
    scan_path: str | None
    library_id: str | None
    event_type: str  # FsEventType value
    file_path: str
    ok: bool
    status_code: int | None
    detail: str


class EventsBus:
    def __init__(self, *, capacity: int = 200) -> None:
        self._buffer: deque[EventRecord] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[EventRecord]] = set()

    def publish(self, record: EventRecord) -> None:
        self._buffer.append(record)
        for queue in self._subscribers:
            if queue.full():
                # drop the oldest queued item so publish never blocks on a slow subscriber
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race with the consumer
                    pass
            queue.put_nowait(record)

    def recent(self, limit: int = 50) -> list[EventRecord]:
        if limit <= 0:
            return []
        records = list(self._buffer)
        return records[-limit:]

    async def subscribe(self) -> AsyncIterator[EventRecord]:
        queue: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
```

> Note: the `except asyncio.QueueEmpty: pass` here guards a genuine producer/consumer race and is the
> one place a bare `try/except` is clearer than `contextlib.suppress` because of the `# pragma`
> comment placement; if ruff `SIM105` flags it, convert to
> `with contextlib.suppress(asyncio.QueueEmpty):` and move the `# pragma: no cover` comment above the
> `with`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_events_bus.py -v
```

Expected: PASS — 7 passed.

- [ ] **Step 5: Lint + type-check**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check mediascanmonitor/observ/events_bus.py tests/web/test_events_bus.py && \
  ruff format --check mediascanmonitor/observ/events_bus.py tests/web/test_events_bus.py && \
  mypy mediascanmonitor/observ/events_bus.py
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found". If ruff rewrites the test's
dead-branch line, re-run `pytest tests/web/test_events_bus.py` to confirm still green.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/observ/events_bus.py tests/web/test_events_bus.py
git commit -m "feat(observ): add EventsBus ring buffer + EventRecord value object"
```

---

### Task 3: `web/auth.py` password surface (functions only)

**Files:**
- Create: `tests/web/test_auth.py`
- Create: `mediascanmonitor/web/auth.py` (functions in this task; the router is added in Task 5)

**Interfaces:**
- Consumes: `Repo.get_setting`/`set_setting` (`db/repo.py`), `argon2.PasswordHasher` +
  `argon2.exceptions` (`argon2-cffi`).
- Produces (contract §C):

```python
PASSWORD_HASH_KEY = "password_hash"

def hash_password(password: str) -> str: ...
def verify_password(stored_hash: str, password: str) -> bool: ...   # False on mismatch/invalid, never raises
def is_password_set(repo: Repo) -> bool: ...
def set_password(repo: Repo, password: str) -> None: ...
def check_password(repo: Repo, password: str) -> bool: ...
def bootstrap_password(repo: Repo) -> None: ...
```

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_auth.py`:

```python
"""web/auth.py password surface: Argon2 hashing, Setting-backed password, env bootstrap."""

from pathlib import Path

import pytest

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web import auth

# `repo` fixture comes from tests/web/conftest.py (Task 4). For this function-level test
# file it is equally satisfied by the same fixture; conftest lands in Task 4, so run this
# file's tests only after Task 4's conftest exists OR add a local repo fixture. To keep
# Task 3 self-contained, this file defines its own minimal repo fixture mirroring
# tests/db/conftest.py.
from cryptography.fernet import Fernet

from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.session import init_db, session_factory


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    engine = init_db(tmp_path / "app.db")
    return Repo(session_factory(engine), SecretBox(Fernet.generate_key()))


def test_hash_and_verify_roundtrip() -> None:
    h = auth.hash_password("hunter2")
    assert h != "hunter2"  # never stored in the clear
    assert h.startswith("$argon2")  # Argon2 PHC string
    assert auth.verify_password(h, "hunter2") is True
    assert auth.verify_password(h, "wrong") is False


def test_verify_password_never_raises_on_garbage_hash() -> None:
    assert auth.verify_password("not-a-hash", "anything") is False
    assert auth.verify_password("", "anything") is False


def test_is_password_set_false_then_true(repo: Repo) -> None:
    assert auth.is_password_set(repo) is False
    auth.set_password(repo, "pw")
    assert auth.is_password_set(repo) is True


def test_check_password(repo: Repo) -> None:
    assert auth.check_password(repo, "pw") is False  # nothing set yet
    auth.set_password(repo, "pw")
    assert auth.check_password(repo, "pw") is True
    assert auth.check_password(repo, "nope") is False


def test_set_password_overwrites(repo: Repo) -> None:
    auth.set_password(repo, "first")
    auth.set_password(repo, "second")
    assert auth.check_password(repo, "second") is True
    assert auth.check_password(repo, "first") is False


def test_bootstrap_noop_when_already_set(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth.set_password(repo, "ui-set")
    monkeypatch.setenv("MSM_PASSWORD", "env-set")
    auth.bootstrap_password(repo)  # idempotent — must NOT overwrite the UI password
    assert auth.check_password(repo, "ui-set") is True
    assert auth.check_password(repo, "env-set") is False


def test_bootstrap_from_env_var(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("MSM_PASSWORD", "from-env")
    auth.bootstrap_password(repo)
    assert auth.check_password(repo, "from-env") is True


def test_bootstrap_file_takes_precedence_over_var(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("  from-file\n")  # whitespace must be stripped
    monkeypatch.setenv("MSM_PASSWORD_FILE", str(pw_file))
    monkeypatch.setenv("MSM_PASSWORD", "from-env")
    auth.bootstrap_password(repo)
    assert auth.check_password(repo, "from-file") is True
    assert auth.check_password(repo, "from-env") is False


def test_bootstrap_does_nothing_without_env(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("MSM_PASSWORD", raising=False)
    auth.bootstrap_password(repo)
    assert auth.is_password_set(repo) is False  # first-run setup screen will handle it


def test_bootstrap_ignores_empty_values(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n")  # whitespace-only file → empty after strip
    monkeypatch.setenv("MSM_PASSWORD_FILE", str(blank))
    monkeypatch.setenv("MSM_PASSWORD", "")  # empty var
    auth.bootstrap_password(repo)
    assert auth.is_password_set(repo) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_auth.py -v
```

Expected: FAIL — `ImportError: cannot import name ... from 'mediascanmonitor.web.auth'` (module
does not exist yet).

- [ ] **Step 3: Implement `web/auth.py` (functions only)**

Create `mediascanmonitor/web/auth.py`. **Task 5 appends the router to this same file** — write the
helper functions now and leave the router for Task 5.

```python
"""Authentication surface: Argon2 password hashing, Setting-backed storage, bootstrap.

All functions here are SYNCHRONOUS (Argon2 is CPU-bound, the Repo is sync SQLModel);
route handlers call them off the event loop via ``asyncio.to_thread`` (contract §C).
The password is stored as an Argon2 PHC string in the ``Setting`` table under
``password_hash`` — never in the clear. ``bootstrap_password`` seeds a first-run
password from the environment but NEVER logs the value (rule 5) and never overwrites a
password already set in the UI.
"""

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from mediascanmonitor.db.repo import Repo

PASSWORD_HASH_KEY = "password_hash"

_hasher = PasswordHasher()  # library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def is_password_set(repo: Repo) -> bool:
    return repo.get_setting(PASSWORD_HASH_KEY) is not None


def set_password(repo: Repo, password: str) -> None:
    repo.set_setting(PASSWORD_HASH_KEY, hash_password(password))


def check_password(repo: Repo, password: str) -> bool:
    stored = repo.get_setting(PASSWORD_HASH_KEY)
    return stored is not None and verify_password(stored, password)


def bootstrap_password(repo: Repo) -> None:
    """Seed a first-run password from the environment (idempotent).

    Precedence: ``MSM_PASSWORD_FILE`` (a path; file contents, whitespace-stripped) then
    ``MSM_PASSWORD``. If a password is already set, return without touching it. If neither
    env source yields a non-empty value, do nothing — the setup screen handles first run.
    Never logs the value.
    """
    if is_password_set(repo):
        return
    value = ""
    file_path = os.environ.get("MSM_PASSWORD_FILE")
    if file_path:
        try:
            value = _read_secret_file(file_path)
        except OSError:
            value = ""
    if not value:
        value = (os.environ.get("MSM_PASSWORD") or "").strip()
    if value:
        set_password(repo, value)


def _read_secret_file(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8").strip()
```

> Note: `_hasher.verify` returns `True` on success and raises on failure — wrapping it so
> `verify_password` returns a `bool` and never propagates. `argon2-cffi`'s default `PasswordHasher`
> needs no tuning for this single-user app.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_auth.py -v
```

Expected: PASS — 11 passed.

- [ ] **Step 5: Lint + type-check**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check mediascanmonitor/web/auth.py tests/web/test_auth.py && \
  ruff format --check mediascanmonitor/web/auth.py tests/web/test_auth.py && \
  mypy mediascanmonitor/web/auth.py
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/auth.py tests/web/test_auth.py
git commit -m "feat(web): Argon2 password surface + env bootstrap (Setting-backed)"
```

---

### Task 4: `create_app` factory + `web/deps.py` + the web test harness

**Files:**
- Create: `tests/web/conftest.py` (**the shared web harness — sub-plans 02/03/04 reuse these exact fixtures**)
- Create: `tests/web/test_deps.py`
- Create: `mediascanmonitor/web/deps.py`
- Create: `mediascanmonitor/web/app.py`
- Create: `mediascanmonitor/web/templates/base.html` (placeholder so `Jinja2Templates` has a dir; finished in Task 5)

**Interfaces:**
- Consumes: `Repo` (`db/repo.py`), `Engine`/`EngineState` (`engine.py`), `EventsBus`
  (`observ/events_bus.py`), `LoginRateLimiter` (`web/ratelimit.py`), `is_password_set` (`web/auth.py`).
- Produces (contract §A/§B):

```python
# web/app.py
def create_app(repo: Repo, engine: Engine, events_bus: EventsBus, *, session_secret: str) -> FastAPI: ...

# web/deps.py
def get_repo(request: Request) -> Repo: ...
def get_engine(request: Request) -> Engine: ...
def get_events_bus(request: Request) -> EventsBus: ...
def get_templates(request: Request) -> Jinja2Templates: ...
async def require_api_auth(request: Request) -> None: ...
async def require_page_auth(request: Request) -> None: ...
```

- [ ] **Step 1: Create the web test harness (`tests/web/conftest.py`)**

This is the harness every later Phase 3 sub-plan assumes. Create `tests/web/conftest.py`:

```python
"""Web test harness (shared by all Phase 3 sub-plans).

`repo` mirrors tests/db/conftest.py (real file-backed SQLite under tmp_path + a fresh
Fernet SecretBox). `engine` is a FakeEngine stub exposing the surface create_app/deps and
the routes touch (`.state`, `.watch_limit`, async `rebuild()`), so web tests never spin up
the real watcher/inotify. `client` is unauthenticated; `auth_client` has a real session
cookie obtained via POST /auth/login after a password is set; `aclient` is an httpx
AsyncClient over ASGITransport for the async SSE smoke (sub-plans 03/04).
"""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session
from starlette.testclient import TestClient

from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.session import init_db, session_factory
from mediascanmonitor.engine import EngineState
from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus
from mediascanmonitor.web.app import create_app
from mediascanmonitor.web.auth import set_password

SESSION_SECRET = "test-secret-key"
AUTH_PASSWORD = "pw"


class FakeEngine:
    """Stand-in for engine.Engine: just the surface the web layer reads/calls."""

    def __init__(self) -> None:
        self.state: EngineState = EngineState.running
        self.watch_limit: WatchLimitStatus | None = None
        self.rebuild_calls = 0

    async def rebuild(self) -> None:
        self.rebuild_calls += 1


@pytest.fixture
def factory(tmp_path: Path) -> Callable[[], Session]:
    engine = init_db(tmp_path / "app.db")
    return session_factory(engine)


@pytest.fixture
def repo(factory: Callable[[], Session]) -> Repo:
    return Repo(factory, SecretBox(Fernet.generate_key()))


@pytest.fixture
def events_bus() -> EventsBus:
    return EventsBus()


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def app(repo: Repo, engine: FakeEngine, events_bus: EventsBus):  # type: ignore[no-untyped-def]
    # FakeEngine is a structural stand-in for engine.Engine; create_app only stores it.
    return create_app(repo, engine, events_bus, session_secret=SESSION_SECRET)  # type: ignore[arg-type]


@pytest.fixture
def client(app) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(app)


@pytest.fixture
def auth_client(app, repo: Repo) -> TestClient:  # type: ignore[no-untyped-def]
    set_password(repo, AUTH_PASSWORD)
    client = TestClient(app)
    resp = client.post(
        "/auth/login", data={"password": AUTH_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text  # session cookie now set on `client`
    return client


@pytest.fixture
async def aclient(app, repo: Repo):  # type: ignore[no-untyped-def]
    # Authenticated async client for the SSE smoke (the stream route is behind require_page_auth).
    # httpx.AsyncClient defaults to follow_redirects=False, so the 303 returns and its Set-Cookie
    # persists in the client's cookie jar for subsequent stream reads.
    set_password(repo, AUTH_PASSWORD)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/auth/login", data={"password": AUTH_PASSWORD})
        assert resp.status_code == 303, resp.text  # session cookie now set on the async client
        yield c
```

> **Harness contract for downstream sub-plans (document, do not change):** `repo` (real Repo on tmp
> sqlite), `events_bus` (`EventsBus()`), `engine` (`FakeEngine`: `.state`, `.watch_limit`, async
> `rebuild()` incrementing `.rebuild_calls`), `app` (`create_app(...)` with
> `session_secret="test-secret-key"`), `client` (unauthenticated `TestClient`), `auth_client`
> (logged-in `TestClient`), `aclient` (**authenticated** `httpx.AsyncClient` over `ASGITransport` — a
> password is set and a login cookie obtained, so it can read the auth-guarded SSE stream; use the
> unauthenticated `client` to assert guard redirects). Sub-plans 02/03/04 extend (never redefine) these.
>
> The `# type: ignore[arg-type]` on `create_app(...)` is because `FakeEngine` is a structural stub,
> not an `engine.Engine` subclass — acceptable in test code; production passes a real `Engine`.

- [ ] **Step 2: Write the failing guard tests**

The guards can't be exercised without at least one guarded route. The factory mounts only the auth
router this sub-plan, so the deps test mounts a tiny probe app of its own that wires both guards onto
sample routes. Create `tests/web/test_deps.py`:

```python
"""Auth guards: API → 401, HTML page → 303 /login; no-password → setup redirect/401."""

import pytest
from fastapi import Depends, FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.auth import set_password
from mediascanmonitor.web.deps import require_api_auth, require_page_auth


def _probe_app(repo: Repo) -> FastAPI:
    app = FastAPI()
    app.state.repo = repo
    app.add_middleware(SessionMiddleware, secret_key="probe-secret", same_site="lax")

    @app.get("/probe/api", dependencies=[Depends(require_api_auth)])
    async def api_route() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/probe/page", dependencies=[Depends(require_page_auth)])
    async def page_route() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_api_guard_401_when_unauthenticated(repo: Repo) -> None:
    set_password(repo, "pw")  # password IS set, just not logged in
    client = TestClient(_probe_app(repo))
    resp = client.get("/probe/api")
    assert resp.status_code == 401


def test_page_guard_303_to_login_when_unauthenticated(repo: Repo) -> None:
    set_password(repo, "pw")
    client = TestClient(_probe_app(repo))
    resp = client.get("/probe/page", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_api_guard_setup_required_when_no_password(repo: Repo) -> None:
    client = TestClient(_probe_app(repo))  # no password set
    resp = client.get("/probe/api")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "setup required"


def test_page_guard_redirects_to_setup_when_no_password(repo: Repo) -> None:
    client = TestClient(_probe_app(repo))  # no password set
    resp = client.get("/probe/page", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


@pytest.mark.parametrize("path", ["/probe/api", "/probe/page"])
def test_guards_allow_when_authenticated(repo: Repo, path: str) -> None:
    set_password(repo, "pw")
    app = _probe_app(repo)

    # add a tiny login endpoint that sets the session, to authenticate the probe client
    @app.post("/login-probe")
    async def login_probe(request: Request) -> dict[str, bool]:
        request.session["authed"] = True
        return {"ok": True}

    client = TestClient(app)
    client.post("/login-probe")
    resp = client.get(path)
    assert resp.status_code == 200
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_deps.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.deps'` (and
`web.app` for the conftest import). That is expected; both land in Steps 4-5.

- [ ] **Step 4: Implement `web/deps.py`**

Create `mediascanmonitor/web/deps.py`:

```python
"""FastAPI dependency providers + auth guards (contract §B).

State accessors read what create_app stored on app.state (sync; no I/O). The two guards
enforce the allow-list: an API route returns JSON 401, an HTML page 303-redirects to
/login (or /setup when no password is set yet, which is what makes first-run setup
reachable while everything else is locked). `is_password_set` is a sync Repo read, so the
guards call it via asyncio.to_thread (cross-plan invariant 3).
"""

import asyncio

from fastapi import HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventsBus

# Import the auth MODULE, not the `is_password_set` symbol: web/app.py imports auth, auth's
# router imports this deps module, and a `from …auth import is_password_set` here would bind a
# name that auth has not defined yet (partially-initialized module → ImportError). Module-attribute
# access (`auth.is_password_set`) is resolved lazily at call time, so it breaks the cycle.
from mediascanmonitor.web import auth


def get_repo(request: Request) -> Repo:
    repo: Repo = request.app.state.repo
    return repo


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_events_bus(request: Request) -> EventsBus:
    bus: EventsBus = request.app.state.events_bus
    return bus


def get_templates(request: Request) -> Jinja2Templates:
    templates: Jinja2Templates = request.app.state.templates
    return templates


def _is_authed(request: Request) -> bool:
    return request.session.get("authed") is True


async def require_api_auth(request: Request) -> None:
    if _is_authed(request):
        return
    repo = get_repo(request)
    if not await asyncio.to_thread(auth.is_password_set, repo):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="setup required")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")


async def require_page_auth(request: Request) -> None:
    if _is_authed(request):
        return
    repo = get_repo(request)
    location = "/login"
    if not await asyncio.to_thread(auth.is_password_set, repo):
        location = "/setup"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": location},
    )
```

> Note: raising `HTTPException(303, headers={"Location": ...})` makes FastAPI emit a redirect with no
> body — the standard pattern for a dependency-level redirect. The page handler never runs when the
> guard raises.

- [ ] **Step 5: Implement `web/app.py` + the placeholder template**

First create a minimal `mediascanmonitor/web/templates/base.html` so `Jinja2Templates(directory=...)`
has a real directory (Task 5 fleshes templates out):

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{% block title %}media-scan-monitor{% endblock %}</title>
  </head>
  <body>
    {% block content %}{% endblock %}
  </body>
</html>
```

Then create `mediascanmonitor/web/app.py`:

```python
"""FastAPI application factory (contract §A).

PURE: takes the already-built Repo / Engine / EventsBus, stores them on app.state, mounts
SessionMiddleware (signs the cookie with itsdangerous; same_site="lax" is the deliberate
CSRF defense — see contract §C), Jinja2 templates, the LoginRateLimiter, and every router,
then returns the app. No I/O, no env reads, no password bootstrap (serve_web does that,
sub-plan 03), so each test builds its own app cheaply.

The session stores exactly one key: "authed" (True once logged in). Later sub-plans append
their include_router(...) lines below — keep every line on merge (the one shared merge
point across Phase 3 sub-plans).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.web import auth
from mediascanmonitor.web.ratelimit import LoginRateLimiter

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def create_app(
    repo: Repo,
    engine: Engine,
    events_bus: EventsBus,
    *,
    session_secret: str,
) -> FastAPI:
    app = FastAPI(title="media-scan-monitor")

    app.state.repo = repo
    app.state.engine = engine
    app.state.events_bus = events_bus
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.limiter = LoginRateLimiter()

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth.router)
    # sub-plan 02: app.include_router(servers.router); folders.router; events.router
    # sub-plan 03: app.include_router(system.router)
    # sub-plan 04: app.include_router(pages.router); app.mount("/static", StaticFiles(...))

    return app
```

> **Heads-up:** `web/app.py` imports `auth.router`, which Task 5 adds. To keep Task 4 independently
> runnable, add a *temporary empty router* in `web/auth.py` now — at the bottom of the file written in
> Task 3, append:
> ```python
> from fastapi import APIRouter
>
> router = APIRouter()
> ```
> Task 5 replaces this stub with the real auth routes. (Alternatively, do Task 5's router in the same
> branch before running the full suite — but the stub keeps each task's tests green in isolation.)

- [ ] **Step 6: Run the deps tests to verify they pass**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_deps.py -v
```

Expected: PASS — 6 passed (4 named + 2 parametrized).

- [ ] **Step 7: Lint + type-check**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check mediascanmonitor/web/ tests/web/ && \
  ruff format --check mediascanmonitor/web/ tests/web/ && \
  mypy mediascanmonitor/web
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found". (`Engine` is imported in
`deps.py`/`app.py` for the annotations and is runtime-importable — no `TYPE_CHECKING` guard, per the
PEP 649 caveat.)

- [ ] **Step 8: Commit**

```bash
git add mediascanmonitor/web/app.py mediascanmonitor/web/deps.py \
        mediascanmonitor/web/templates/base.html mediascanmonitor/web/auth.py \
        tests/web/conftest.py tests/web/test_deps.py
git commit -m "feat(web): create_app factory, DI deps, auth guards, web test harness"
```

---

### Task 5: Auth router + login/setup templates

**Files:**
- Modify: `mediascanmonitor/web/auth.py` (replace the stub `router` with the real routes)
- Create: `mediascanmonitor/web/templates/login.html`
- Create: `mediascanmonitor/web/templates/setup.html`
- Modify: `mediascanmonitor/web/templates/base.html` (add an error/flash block the forms render into)
- Create: `tests/web/test_auth_routes.py`

**Interfaces:**
- Consumes: `Form` (FastAPI, needs `python-multipart`), `check_password`/`set_password`/
  `is_password_set` (`web/auth.py`), `LoginRateLimiter` on `app.state.limiter`, `get_repo`/
  `get_templates` (`web/deps.py`), `require_page_auth` (for logout / change-password).
- Produces (contract §C auth route table):

| route | auth | behavior |
|---|---|---|
| `POST /auth/login` | none | `Form(password=...)`. Rate-limit by IP → `429` if blocked. Success: `limiter.reset`, `session["authed"]=True`, `303` to `/`. Failure: `limiter.record_failure`, re-render `/login` with an error (`401`). |
| `POST /auth/logout` | required | `session.clear()`, `303` to `/login`. |
| `POST /auth/password` | required | `Form(current_password=..., new_password=...)`. Verify current; success → `set_password(new)` (no rebuild); failure → re-render with an error. |
| `GET /login` | none | render the login form. |
| `GET /setup` + `POST /setup` | none, only while `not is_password_set` | first-run password creation; `POST` → `set_password` then log in (`303` to `/`); once a password exists, redirect to `/login`. |

> **CSRF (contract §C):** these POSTs are authenticated by the `same_site="lax"` session cookie, which
> the browser withholds on cross-site POSTs — that is the deliberate CSRF defense, so no CSRF token is
> used. Stated here so the omission is not read as an oversight.

- [ ] **Step 1: Write the failing route tests**

Create `tests/web/test_auth_routes.py`:

```python
"""Auth router flows: login success/failure, rate-limit lockout, logout, change-pw, setup."""

from starlette.testclient import TestClient

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.auth import check_password, is_password_set, set_password

from .conftest import AUTH_PASSWORD


def test_login_success_sets_session_and_redirects(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # session now authed: a guarded request would pass (probe via logout, which requires auth)
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.status_code == 303


def test_login_failure_returns_401_and_records_failure(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "wrong"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "incorrect" in resp.text.lower() or "invalid" in resp.text.lower()


def test_login_rate_limit_locks_out_after_max_attempts(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    # default limiter max_attempts=5: 5 failures, then the 6th attempt is 429
    for _ in range(5):
        r = client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
        assert r.status_code == 401
    locked = client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
    assert locked.status_code == 429


def test_login_success_resets_rate_limit(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    for _ in range(4):
        client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
    ok = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert ok.status_code == 303  # under the limit; success resets the counter


def test_logout_requires_auth(app) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    # not logged in, password not set → guard sends to setup
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code in (303, 401)


def test_logout_clears_session(auth_client: TestClient) -> None:
    resp = auth_client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    # session cleared: logout again now redirects to /login via the guard (no longer authed)
    again = auth_client.post("/auth/logout", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/login"


def test_change_password_success(auth_client: TestClient, repo: Repo) -> None:
    resp = auth_client.post(
        "/auth/password",
        data={"current_password": AUTH_PASSWORD, "new_password": "brand-new"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303)
    assert check_password(repo, "brand-new") is True
    assert check_password(repo, AUTH_PASSWORD) is False


def test_change_password_wrong_current_is_rejected(
    auth_client: TestClient, repo: Repo
) -> None:
    resp = auth_client.post(
        "/auth/password",
        data={"current_password": "nope", "new_password": "brand-new"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 400, 401)
    assert check_password(repo, AUTH_PASSWORD) is True  # unchanged


def test_get_login_renders_form(client: TestClient) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


def test_get_setup_renders_when_no_password(client: TestClient) -> None:
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


def test_post_setup_creates_password_and_logs_in(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    resp = client.post("/setup", data={"password": "first-pw"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert is_password_set(repo) is True
    # logged in: logout works (requires auth) and redirects to /login
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.status_code == 303


def test_setup_blocked_once_password_exists(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "already")
    client = TestClient(app)
    get_resp = client.get("/setup", follow_redirects=False)
    assert get_resp.status_code == 303
    assert get_resp.headers["location"] == "/login"
    post_resp = client.post("/setup", data={"password": "hijack"}, follow_redirects=False)
    assert post_resp.status_code == 303
    assert post_resp.headers["location"] == "/login"
    assert check_password(repo, "already") is True  # not overwritten
    assert check_password(repo, "hijack") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_auth_routes.py -v
```

Expected: FAIL — the stub `router` (Task 4) has no routes, so `/auth/login` etc. return `404`.

- [ ] **Step 3: Replace the stub router in `web/auth.py` with the real routes**

In `mediascanmonitor/web/auth.py`, remove the temporary `router = APIRouter()` stub added in Task 4
and append the full router below the helper functions. Add the needed imports to the top of the file
(keep stdlib / third-party / first-party groups separated by a blank line — `mediascanmonitor` is
first-party):

```python
import asyncio

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.deps import get_repo, get_templates, require_page_auth
from mediascanmonitor.web.ratelimit import LoginRateLimiter
```

Then append the router and handlers:

```python
router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _limiter(request: Request) -> LoginRateLimiter:
    limiter: LoginRateLimiter = request.app.state.limiter
    return limiter


@router.get("/login")
async def login_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/auth/login")
async def login(
    request: Request,
    password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    limiter = _limiter(request)
    key = _client_ip(request)
    if not limiter.allowed(key):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many attempts. Try again later."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if await asyncio.to_thread(check_password, repo, password):
        limiter.reset(key)
        request.session["authed"] = True
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    limiter.record_failure(key)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Incorrect password."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@router.post("/auth/logout", dependencies=[Depends(require_page_auth)])
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/password", dependencies=[Depends(require_page_auth)])
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if not await asyncio.to_thread(check_password, repo, current_password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Current password is incorrect."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await asyncio.to_thread(set_password, repo, new_password)  # no rebuild: auth is not engine config
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/setup")
async def setup_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if await asyncio.to_thread(is_password_set, repo):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup(
    request: Request,
    password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if await asyncio.to_thread(is_password_set, repo):
        # setup can never reset an existing password
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not password.strip():
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "Password must not be empty."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await asyncio.to_thread(set_password, repo, password)
    request.session["authed"] = True
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
```

> **Import-cycle handling (already applied above):** `web/auth.py` imports symbols from `web/deps.py`
> at module top, and `web/deps.py` in turn needs `is_password_set` from `web/auth.py`. Importing the
> *symbol* (`from web.auth import is_password_set`) would fail: `web/app.py` → imports `auth` → `auth`
> imports `deps` → `deps` tries to bind `is_password_set` from the still-initializing `auth` module →
> `ImportError: cannot import name 'is_password_set' from partially initialized module`. That is why
> `deps.py` imports the **module** (`from mediascanmonitor.web import auth`) and calls
> `auth.is_password_set(...)` — module-attribute access resolves at request time, after both modules
> have finished importing. Do **not** revert it to a symbol import. (If a cycle ever resurfaces, the
> fallback is a function-local import inside the handler.)

- [ ] **Step 4: Create the templates**

Update `mediascanmonitor/web/templates/base.html` to expose an `error` flash block:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{% block title %}media-scan-monitor{% endblock %}</title>
    <link rel="stylesheet" href="/static/app.css" />
    <script src="/static/htmx.min.js" defer></script>
  </head>
  <body>
    {% if error %}<p role="alert" class="error">{{ error }}</p>{% endif %}
    {% block content %}{% endblock %}
  </body>
</html>
```

> The `/static/app.css` + `/static/htmx.min.js` references are declared here in the shared shell so
> every page inherits them, but the **files and the `/static` mount land in sub-plan 04**. Until then
> they simply 404 (the login/setup pages in this sub-plan need no CSS or htmx, so the unstyled forms
> still work and all Task-5 tests pass). Sub-plan 04 adds `StaticFiles` at `/static` + the vendored
> `htmx.min.js` + `app.css`, at which point the htmx-driven dashboard becomes functional in a browser.

Create `mediascanmonitor/web/templates/login.html`:

```html
{% extends "base.html" %}
{% block title %}Sign in — media-scan-monitor{% endblock %}
{% block content %}
<h1>Sign in</h1>
<form method="post" action="/auth/login">
  <label>Password <input type="password" name="password" autofocus required /></label>
  <button type="submit">Sign in</button>
</form>
{% endblock %}
```

Create `mediascanmonitor/web/templates/setup.html`:

```html
{% extends "base.html" %}
{% block title %}First-run setup — media-scan-monitor{% endblock %}
{% block content %}
<h1>Set an admin password</h1>
<p>No password is set yet. Choose one to secure the dashboard.</p>
<form method="post" action="/setup">
  <label>Password <input type="password" name="password" autofocus required /></label>
  <button type="submit">Create password</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run the route tests to verify they pass**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/test_auth_routes.py -v
```

Expected: PASS — 12 passed.

- [ ] **Step 6: Run the whole web suite**

```bash
export PATH="$PWD/.venv/bin:$PATH"
pytest tests/web/ -v
```

Expected: PASS — all web tests green (ratelimit + events_bus + auth + deps + auth_routes).

- [ ] **Step 7: Lint + type-check**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check mediascanmonitor/web tests/web && \
  ruff format --check mediascanmonitor/web tests/web && \
  mypy mediascanmonitor/web
```

Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 8: Commit**

```bash
git add mediascanmonitor/web/auth.py mediascanmonitor/web/templates/ \
        tests/web/test_auth_routes.py
git commit -m "feat(web): auth router (login/logout/change-pw/first-run setup) + templates"
```

---

### Task 6: Full-suite verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full gate**

```bash
export PATH="$PWD/.venv/bin:$PATH"
ruff check . && ruff format --check . && mypy mediascanmonitor && pytest
```

Expected: all green. Confirm:
- the two new deps resolve under `uv sync --locked` (Task 1),
- no existing Phase 1/2 test changed (this sub-plan only **adds** modules + `web/templates` and edits
  `pyproject.toml`/`uv.lock`; it does **not** touch `engine.py`, `cli.py`, or any adapter),
- `pytest tests/web/` covers ratelimit, events bus, auth helpers, guards, and every auth route.

- [ ] **Step 2: (If the gate is green and nothing else is staged) no extra commit needed**

The per-task commits already capture the work. If `ruff format` rewrote anything during the gate,
re-stage and amend the relevant task commit, then re-run the gate.

---

## Self-Review

**Spec coverage (contract sections this sub-plan owns):**

- **§A app factory** — `create_app(repo, engine, events_bus, *, session_secret)` stores the five
  `app.state` keys (`repo`/`engine`/`events_bus`/`templates`/`limiter`), mounts
  `SessionMiddleware(same_site="lax", https_only=False)` + `Jinja2Templates(web/templates)` + the auth
  router; pure (no I/O). Task 4. ✓
- **§B deps + guards** — `get_repo`/`get_engine`/`get_events_bus`/`get_templates`; `require_api_auth`
  (401, "setup required" when unset) / `require_page_auth` (303 → `/login`, or `/setup` when unset);
  `is_password_set` read off-thread. Task 4 (`test_deps.py`). ✓
- **§C auth + routes** — `hash_password`/`verify_password`/`is_password_set`/`set_password`/
  `check_password`/`bootstrap_password` (Task 3); login (rate-limit→429, session, 401 re-render),
  logout, change-password, `GET/POST /setup` while-unset (Task 5); CSRF-via-SameSite rationale stated.
  ✓
- **§G EventsBus/EventRecord class only** — frozen `EventRecord` (no secret field), `EventsBus`
  `publish`/`recent`/`subscribe` with `deque(maxlen=capacity)` + per-subscriber bounded
  `asyncio.Queue` (drop-oldest). Engine wiring explicitly deferred to sub-plan 03. Task 2. ✓
- **`web/ratelimit.py` LoginRateLimiter** — `allowed`/`record_failure`/`reset`, per-IP,
  `time.monotonic` with an injectable `now`. Task 1. ✓
- **Deps** — `itsdangerous` + `python-multipart` pinned with re-verify instruction + add-time comment;
  `uv lock` + commit `pyproject.toml`+`uv.lock`. Task 1. ✓
- **Web test harness** — `tests/web/conftest.py` with `repo`/`events_bus`/`engine`(FakeEngine)/`app`/
  `client`/`auth_client`/`aclient`, documented for downstream reuse. Task 4. ✓

**Placeholder scan:** none — complete code + exact commands in every step.

**Signature fidelity:** `create_app`/deps/auth/ratelimit/EventsBus signatures match contract
§A/§B/§C/§G verbatim; `EventRecord` fields are the `TriggerResult` (`ok`/`status_code`/`detail`) +
`ScanRequest` context union 03 needs; `Repo.get_setting`/`set_setting` and `Setting` key
`password_hash` are the real Phase 1 surfaces; the harness `repo` fixture mirrors
`tests/db/conftest.py` (`init_db(tmp_path/'app.db')` + `session_factory` + `SecretBox(Fernet
.generate_key())`).

**Boundary discipline:** `Form(...)` (python-multipart) at the request boundary; templates render
validated context; Argon2/Repo calls go through `asyncio.to_thread` in every handler/guard; no secret
is logged or returned.

## Verification

Final gate (run after Task 6, with `export PATH="$PWD/.venv/bin:$PATH"` already done):

```bash
ruff check . && ruff format --check . && mypy mediascanmonitor && pytest
```

Must be green. CI (`.github/workflows/ci.yml`) runs the same on Python 3.14 via `uv sync --locked`,
so the refreshed `uv.lock` from Task 1 must be committed.

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-20-phase3-01-app-auth.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session using `executing-plans`, batched with checkpoints.

Which approach?
