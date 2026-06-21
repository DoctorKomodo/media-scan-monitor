# Phase 3 — Shared Web Interface Contract (FROZEN)

> **For agentic workers:** this is the frozen vocabulary for the Phase 3 web layer. Read it before
> any Phase 3 sub-plan. Each section names the module that **owns** it and the sub-plan that builds
> it. A sub-plan may not change a signature here without updating this file **and** every consumer.
> Anything not nailed down here (HTML structure, CSS, exact SQL) is the owning sub-plan's choice.

This phase adds the FastAPI app around the existing engine/repo/adapters. It consumes the Phase 1
frozen contract verbatim (`Repo`, `Engine`, `RuntimeConfig`, `ServerAdapter`, the SQLModel tables,
`check_watch_limit`) and adds the web vocabulary below.

## 0. Conventions

- **Package layout (new):**
  ```
  mediascanmonitor/web/
    __init__.py
    app.py          # create_app() factory + middleware wiring            (sub-plan 01)
    deps.py         # FastAPI DI providers + auth guards                   (sub-plan 01)
    auth.py         # Argon2 hashing, Setting-backed password, bootstrap   (sub-plan 01)
    ratelimit.py    # in-memory login rate limiter                         (sub-plan 01)
    api_schemas.py  # redacted read-models + server-type field specs       (sub-plan 02)
    rebuild.py      # rebuild-on-write helper                              (sub-plan 02)
    server.py       # serve_web(): uvicorn + engine on one loop            (sub-plan 03)
    pages.py        # HTML page routes + /ui form handlers + SSE           (sub-plan 04)
    api/
      __init__.py
      servers.py    # /api/servers CRUD + test                            (sub-plan 02)
      folders.py    # /api/servers/{id}/folders CRUD                       (sub-plan 02)
      events.py     # /api/events/recent                                   (sub-plan 02)
      system.py     # /health, /ready, /api/status                         (sub-plan 03)
    templates/      # Jinja2: base/login/setup (01); dashboard/... (04)
    static/         # htmx.min.js + app.css                                (04)
  mediascanmonitor/observ/events_bus.py   # EventsBus+EventRecord class (01); engine publish wiring (03)
  ```
- **Async/sync bridge:** every `Repo` call and every `check_watch_limit` call from a coroutine goes
  through `await asyncio.to_thread(...)` (the repo is sync SQLModel; `check_watch_limit` does
  blocking `os.walk` + a `/proc` read). Never call them directly in an `async def`. Adapter
  `test()`/`trigger()` are already async — `await` them directly.
- **Enums** are the existing `StrEnum`s (`ServerType`, `ScanMode`, `DebounceMode`, `EngineState`,
  `FsEventType`). `str(member)` is the bare value. PEP 649 everywhere; no `from __future__`.
- **Tests:** Starlette `TestClient` (sync) for routes/auth; `httpx.ASGITransport` + `AsyncClient`
  for the async SSE smoke. No real network/inotify/backend.

---

## A. App factory & state — `web/app.py` (owned by sub-plan 01)

```python
def create_app(
    repo: Repo,
    engine: Engine,
    events_bus: EventsBus,
    *,
    session_secret: str,
) -> FastAPI: ...
```

- Builds the `FastAPI` instance, stores its dependencies on `app.state`, mounts middleware, routers,
  templates and static files, and returns it. **Pure**: no I/O, no env reads, no password bootstrap
  (that is the caller's job — `serve_web`, §J). Constructing it never touches the network or the DB,
  so every test builds its own app cheaply.
- **`app.state` keys (frozen — deps read these):**
  | key | type | set by |
  |---|---|---|
  | `app.state.repo` | `Repo` | 01 |
  | `app.state.engine` | `Engine` | 01 |
  | `app.state.events_bus` | `EventsBus` | 01 |
  | `app.state.templates` | `Jinja2Templates` | 01 (dir `web/templates`) |
  | `app.state.limiter` | `LoginRateLimiter` | 01 |
- **Middleware:** `SessionMiddleware(secret_key=session_secret, same_site="lax", https_only=False)`
  (Starlette; signs the cookie with `itsdangerous`). The session stores exactly one key: `"authed"`
  (`True` once logged in). No other session state.
- **Mounting (each router owned by its sub-plan; `create_app` calls `include_router` for all of
  them — the one shared merge point across sub-plans, resolve by keeping every `include_router`
  line):** auth router (01), `api/servers` `api/folders` `api/events` (02), `api/system` (03), pages
  router (04), `StaticFiles` at `/static` (04). Until a later sub-plan lands its router, `create_app`
  simply does not include it yet; adding the include line is part of that sub-plan.

---

## B. Dependencies & auth guards — `web/deps.py` (owned by sub-plan 01)

State accessors (plain, sync — they only read `app.state`):

```python
def get_repo(request: Request) -> Repo: ...
def get_engine(request: Request) -> Engine: ...
def get_events_bus(request: Request) -> EventsBus: ...
def get_templates(request: Request) -> Jinja2Templates: ...
```

Auth guards (two variants — **API returns JSON `401`; HTML pages `303`-redirect to `/login`**):

```python
async def require_api_auth(request: Request) -> None: ...   # raises HTTPException(401) if not authed
async def require_page_auth(request: Request) -> None: ...   # raises HTTPException(303, Location=/login or /setup)
```

- **Authed** ⇔ `request.session.get("authed") is True`.
- When **no password is set yet** (`is_password_set(repo)` is `False`), both guards send the user to
  first-run setup: `require_api_auth` → `401` with detail `"setup required"`; `require_page_auth` →
  `303` to `/setup`. This is what makes the setup flow reachable while everything else is locked.
- Guards read `is_password_set` via `await asyncio.to_thread(...)`.
- **Allow-list (no guard):** `POST /auth/login`, `GET /login`, `GET /setup` + `POST /setup` (only
  while unset), `GET /health`, and `/static/*`. Everything else — including `/ready`, `/api/status`,
  `/api/*`, all pages, the SSE stream, and Phase 4's `/metrics` — is guarded. Apply guards with
  router-level `dependencies=[Depends(require_api_auth)]` (api routers) / `Depends(require_page_auth)`
  (page router), not per-handler.

---

## C. Auth & session surface — `web/auth.py` + `web/ratelimit.py` (owned by sub-plan 01)

`web/auth.py` (all sync — call from handlers via `asyncio.to_thread`):

```python
PASSWORD_HASH_KEY = "password_hash"   # Setting key

def hash_password(password: str) -> str: ...                 # argon2.PasswordHasher().hash
def verify_password(stored_hash: str, password: str) -> bool # False on mismatch/invalid, never raises
def is_password_set(repo: Repo) -> bool: ...                 # repo.get_setting(PASSWORD_HASH_KEY) is not None
def set_password(repo: Repo, password: str) -> None: ...     # repo.set_setting(PASSWORD_HASH_KEY, hash_password(...))
def check_password(repo: Repo, password: str) -> bool: ...   # stored is not None and verify_password(stored, password)
def bootstrap_password(repo: Repo) -> None: ...              # see below
```

- Uses **`argon2-cffi` directly**: one module-level `argon2.PasswordHasher()` with library defaults.
  `verify_password` catches `argon2.exceptions.VerifyMismatchError` **and** `InvalidHashError`/
  `VerificationError` → returns `False`; a correct password returns `True`.
- **`bootstrap_password(repo)`** (called once at startup by `serve_web`, §J): if `is_password_set`
  is already `True`, return (idempotent — never overwrites a UI-set password). Else read
  `MSM_PASSWORD_FILE` (a path; file contents, whitespace-stripped) **then** `MSM_PASSWORD`; if either
  yields a non-empty value, `set_password`. If neither is set, do nothing — the first-run setup
  screen handles it. Never log the value.
- **Session:** login sets `request.session["authed"] = True`; logout calls
  `request.session.clear()`. The cookie is signed (not encrypted) by `SessionMiddleware`; it carries
  no secret, only the boolean.

**Auth routes (owned by sub-plan 01, in the auth router):**

| route | auth | behavior |
|---|---|---|
| `POST /auth/login` | none (allow-list) | `Form(password=...)`. Rate-limit by IP (§ratelimit); `429` if blocked. On `check_password` success: `limiter.reset`, set session `authed`, `303` to `/`. On failure: `limiter.record_failure`, re-render `/login` with an error (HTML) / `401` (if called as JSON). |
| `POST /auth/logout` | required | `request.session.clear()`, `303` to `/login`. |
| `POST /auth/password` | required | `Form(current_password=..., new_password=...)`. Verify `check_password(current)`; on success `set_password(new)` (no rebuild — auth is not engine config); on failure re-render with an error. (PLAN line 240 "changeable later in the UI".) |
| `GET /setup` + `POST /setup` | none, **only while `not is_password_set`** | first-run password creation; `POST` calls `set_password` then logs the user in (`303` to `/`). Once a password exists, both `404`/redirect to `/login` so setup can't reset the password. |

- **CSRF:** these POSTs are authenticated by the `same_site="lax"` session cookie (§A), which the
  browser withholds on cross-site POSTs — that is the deliberate CSRF defense, so no CSRF token is
  used. State this in the sub-plan so the omission is not read as an oversight.

`web/ratelimit.py` — in-memory, per-client-IP login throttle (no persistence; resets on restart):

```python
class LoginRateLimiter:
    def __init__(self, *, max_attempts: int = 5, window_seconds: float = 300.0) -> None: ...
    def allowed(self, key: str) -> bool: ...        # False once >= max_attempts failures inside the window
    def record_failure(self, key: str) -> None: ...
    def reset(self, key: str) -> None: ...          # call on successful login
```

- `key` is the client IP (`request.client.host`, or `"unknown"`). Uses `time.monotonic()`. A blocked
  login returns `429`. Stored on `app.state.limiter` so it is shared across requests and injectable
  in tests (construct with a tiny window/maxattempts).

---

## D. API read-schemas & server-type specs — `web/api_schemas.py` (owned by sub-plan 02)

Redacted **read** models (Pydantic). Writes reuse `ServerCreate`/`ServerUpdate`/`FolderCreate`/
`FolderUpdate` from `db/schemas.py` (§E) — those already keep plaintext `secret` out of `repr`.

```python
class FolderRead(BaseModel):
    id: int
    server_id: int
    path: str
    library_id: str | None
    enabled: bool
    extensions: list[str]                 # sorted normalized extensions
    @classmethod
    def from_model(cls, folder: Folder) -> FolderRead: ...

class ServerRead(BaseModel):
    id: int
    name: str
    type: ServerType
    base_url: str
    verify_tls: bool
    timeout_seconds: float
    has_secret: bool                      # server.secret_encrypted is not None — NEVER the token/ciphertext
    scan_mode: ScanMode
    debounce_mode: DebounceMode
    debounce_window_seconds: int
    retry_attempts: int
    enabled: bool
    supported_scan_modes: list[ScanMode]  # registry.get_adapter_class(type).supported_scan_modes, sorted
    webhook_method: str | None
    webhook_headers_json: str | None      # NOT a secret column (the token is rendered in only at request time)
    webhook_body_template: str | None
    folders: list[FolderRead]
    @classmethod
    def from_model(cls, server: Server, folders: list[Folder]) -> ServerRead: ...

class ServerTestResponse(BaseModel):
    ok: bool
    detail: str
```

- **Invariant:** no read-schema ever carries the secret or its ciphertext. The only secret signal is
  `has_secret`. (CLAUDE rule 5 / contract invariant 3.)

Server-type field map — the **one** place the app knows per-type rules (rule 2: no type literals
scattered through routers/templates):

```python
@dataclass(frozen=True, slots=True)
class ServerTypeSpec:
    requires_secret: bool     # a token is mandatory at save time
    requires_base_url: bool   # base_url must be non-empty at save time
    is_webhook: bool          # exposes the webhook_* template fields

SERVER_TYPE_SPECS: dict[ServerType, ServerTypeSpec] = {
    ServerType.plex:           ServerTypeSpec(requires_secret=True,  requires_base_url=True,  is_webhook=False),
    ServerType.emby:           ServerTypeSpec(requires_secret=True,  requires_base_url=True,  is_webhook=False),
    ServerType.jellyfin:       ServerTypeSpec(requires_secret=True,  requires_base_url=True,  is_webhook=False),
    ServerType.audiobookshelf: ServerTypeSpec(requires_secret=True,  requires_base_url=True,  is_webhook=False),
    ServerType.webhook:        ServerTypeSpec(requires_secret=False, requires_base_url=False, is_webhook=True),
}
```

- **Token-required validation (FOLLOWUPS Phase-3 item):** on `POST`/`PATCH` of a server, if the
  resulting record would have `requires_secret` but no stored/incoming secret, the API rejects it
  with `422` (detail names the missing token) — the misconfiguration is caught at the boundary, not
  as a late backend `401`. "Resulting" accounts for `ServerUpdate` tri-state: a PATCH that omits
  `secret` keeps the existing one (still valid); a PATCH that sets `secret=None` clears it (now
  invalid for an auth-required type). Webhook is exempt (`requires_secret=False`).

---

## E. Repo & write-schema additions — `db/repo.py`, `db/schemas.py` (owned by sub-plan 02)

Phase 3 adds folder **read-one** + **update** (no schema migration — only new methods/models):

```python
# db/schemas.py
class FolderUpdate(BaseModel):
    path: str | None = None             # validator: if provided, normalize + require absolute (same as FolderCreate)
    library_id: str | None = None
    extensions: list[str] | None = None # validator: if provided, normalize/drop-empty/dedupe
    enabled: bool | None = None

# db/repo.py (Repo)
def get_folder(self, folder_id: int) -> Folder | None: ...   # filetypes force-loaded while session open
def update_folder(self, folder_id: int, data: FolderUpdate) -> Folder: ...
    # KeyError if missing. Applies path/library_id/enabled from model_dump(exclude_unset=True);
    # if `extensions` is present (not None), replaces all FileType rows using the same normalize
    # rule as set_filetypes. Returns the updated Folder with filetypes loaded.
```

- `update_folder`'s `exclude_unset` tri-state mirrors `update_server`: an omitted field is left
  unchanged; `extensions=[]` clears all file-types (→ "match all", cross-plan invariant 1).

---

## F. Rebuild-on-write — `web/rebuild.py` (owned by sub-plan 02)

```python
async def rebuild_engine(engine: Engine) -> None: ...
```

- Calls `await engine.rebuild()` after any successful config write. **Tolerant:** catches
  `RuntimeError` (engine not started) and logs `web.rebuild_skipped` at INFO — a write must never
  `500` because the watcher is detached. After §I lands, `rebuild()` itself handles the `blocked`
  state internally; this helper keeps the guard as defense-in-depth.
- Every CRUD handler that mutates server/folder/filetype state (§D/§E writes) calls this **after** the
  repo commit and **before** returning the response.

---

## G. Events bus — `observ/events_bus.py` + Engine wiring

**Ownership is split** (so the build stays forward-only): the **`EventsBus` + `EventRecord` classes
are created in sub-plan 01** — `create_app`'s signature (§A) references `EventsBus` and stores the
instance on `app.state`, so the class must exist with the foundation. The **Engine publish wiring**
(the `events_bus` ctor param + the `_dispatch` publish) is **sub-plan 03**.

```python
@dataclass(frozen=True, slots=True)
class EventRecord:
    ts: str               # ISO-8601 UTC, e.g. "2026-06-20T18:30:00+00:00"
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
    # NB: no secret field — nothing here may carry a token (rule 5).

class EventsBus:
    def __init__(self, *, capacity: int = 200) -> None: ...
    def publish(self, record: EventRecord) -> None: ...           # ring-buffer append + fan-out to live subscribers
    def recent(self, limit: int = 50) -> list[EventRecord]: ...   # newest-last, at most `limit`
    def subscribe(self) -> AsyncIterator[EventRecord]: ...        # async generator; unregisters its queue on close
```

- Ring buffer = `collections.deque(maxlen=capacity)`. Each subscriber gets a bounded `asyncio.Queue`;
  `publish` does `put_nowait` and drops the **oldest** queued item if a slow subscriber's queue is
  full (the SSE client just misses a beat — never blocks `publish`). `publish` is sync and
  non-blocking so it is safe to call from `Engine._dispatch`.
- **Engine wiring (modifies `engine.py`):** the `Engine.__init__` gains `events_bus: EventsBus | None
  = None`. `Engine._dispatch(req)` captures the `TriggerResult` from `dispatcher.dispatch(req)` and,
  if a bus is present, builds an `EventRecord` (`ts = datetime.now(UTC).isoformat()`) and
  `bus.publish(...)`. No change to the `Dispatcher`. `events_bus=None` ⇒ today's behavior exactly
  (the Phase 1/2 tests that build `Engine(repo)` keep passing).

---

## H. System & status surface — `web/api/system.py` (owned by sub-plan 03)

```python
class StatusRead(BaseModel):
    engine_state: str                 # EngineState value: starting|running|blocked|stopped
    inotify_gate: str                 # "enforce" | "off" (Setting; default "enforce")
    watch_current: int | None         # from engine.watch_limit (None until evaluated / no watches)
    watch_dirs: int | None
    watch_needed: int | None
    watch_recommended: int | None
    watch_ok: bool | None
    server_count: int
    enabled_server_count: int
```

| route | auth | behavior |
|---|---|---|
| `GET /health` | none | always `200 {"status":"ok"}` — liveness; the UI stays reachable even when the engine is `blocked`. |
| `GET /ready` | required | `200` when DB reachable **and** `engine.state is EngineState.running`; else `503` with the state. This single condition **subsumes** PLAN's three ("DB reachable, watcher attached, inotify gate passed"): the engine only reaches `running` when the watcher is attached and the gate passed (§I), so the implementer does not re-derive "watcher attached" separately. Empty config ⇒ engine reaches `running`, so a fresh install is ready. |
| `GET /api/status` | required | `StatusRead` — drives the dashboard. Reads `engine.state`, `engine.watch_limit`, the `inotify_gate` setting, and server counts (repo calls off-thread). |
| `PUT /api/settings/inotify-gate` | required | Body `{"inotify_gate": "enforce" \| "off"}` (validated against a 2-member literal). `repo.set_setting("inotify_gate", value)` off-thread, then `rebuild_engine` (§F) so flipping to `off` recovers a `blocked` engine with no restart (§I `blocked→running`). Returns `StatusRead`. **This is the user-facing half of gate-recovery.** |
| `POST /api/engine/recheck` | required | No body. Re-evaluate the gate after an *out-of-band* fix (the user raised the host `fs.inotify.max_user_watches`, which triggers no config write). Calls `rebuild_engine` (§F) and returns `StatusRead`. Without this, raising the host limit could not move the engine `blocked→running` (PLAN line 308). |

The `/settings` page (§K, sub-plan 04) drives the gate toggle and a "re-check" button via these two
routes (or `/ui` HTML twins sharing the same handler core). `inotify_gate` is the only singleton
setting exposed for now; the route is named for it rather than a generic key/value write so the value
domain stays validated.

---

## I. Engine gate-recovery (blocked ↔ running) — `engine.py` (owned by sub-plan 03)

Refines the Phase 1 engine so a config/gate change recovers **without a restart** (FOLLOWUPS item #7;
PLAN "no-deadlock rule"). Public signatures stay stable except one additive keyword:

```python
async def start(self, *, park_when_blocked: bool = True) -> None: ...   # added kwarg; web uses default, headless passes False
async def rebuild(self) -> None: ...                                    # unchanged signature
```

**Key representation (this is the whole trick — there is NO new "supervised loop" or wake `Event`):**
`blocked` is **not** "watcher detached." For the web path, `blocked` means **the watcher is attached
but watching zero roots**, while the normal `async for event in self._watcher.events()` consume loop
stays alive. Because the watcher has no roots it yields no events, so the engine simply idles. That
turns every gate transition into the *already-proven* dynamic root swap (`set_roots`, engine.py:146,
covered by `test_rebuild_adds_then_removes_roots_and_reroutes`) plus a state-flag flip — no loop
interruption, no parking primitive.

Factor a helper used by both `start()` and `rebuild()`:

```python
def _gate_ok(self, config: RuntimeConfig) -> bool: ...
    # True if not config.watch_paths  OR  inotify_gate policy == "off"  OR  check_watch_limit(...).ok
    # Side effect: sets self.watch_limit (None when there are no watch_paths) for /api/status + /ready.
    # The Repo/get_setting and check_watch_limit calls run via asyncio.to_thread.
```

**`start(park_when_blocked=True)` (web)** — wire the pipeline (adapters/dispatcher/debouncer/watcher)
**unconditionally** (none of this needs the gate; `_build_adapters` does no network I/O), then:

```
gate_ok = await self._gate_ok(config)
self._watcher.set_roots(set(config.watch_paths) if gate_ok else set())   # blocked ⇒ zero roots
self.state = running if gate_ok else blocked
async for event in self._watcher.events():   # ALWAYS entered; idles when roots are empty
    await self._handle_event(event)
```

**`start(park_when_blocked=False)` (headless `serve_headless`)** — same wiring, but on a failed gate
it does **not** attach or loop; it sets `state = blocked` and **returns**, preserving the Bash-style
block→exit-3 behavior (and keeping `watcher.roots_history == []`, which the headless test asserts):

```
gate_ok = await self._gate_ok(config)
if not gate_ok:
    self.state = blocked
    return                      # serve_headless sees the task complete → exit 3
self._watcher.set_roots(set(config.watch_paths)); self.state = running
async for event in self._watcher.events(): ...
```

**`rebuild()`** keeps its atomic snapshot swap, then re-evaluates the gate and re-points the roots —
covering all four transitions without a restart and **without raising**:

| from → to | trigger | action inside `rebuild()` |
|---|---|---|
| `blocked → running` | user raises the kernel limit (then hits *re-check*, §H) **or** flips `inotify_gate=off` | `_gate_ok` now True → `set_roots(watch_paths)`, `state=running` |
| `running → blocked` | new config outgrows the limit under `enforce` | `_gate_ok` now False → `set_roots(∅)`, `state=blocked` |
| `running → running` | ordinary config edit, gate still ok | dynamic root diff (today's behavior) |
| `blocked → blocked` | edit while still over the limit | `set_roots(∅)` stays, `state=blocked` |

`rebuild()` before `start()` still raises `RuntimeError` (caught by §F). The observable caller
contract is unchanged: `await engine.start()` blocks until `aclose()` (web) or returns on
blocked-under-`park_when_blocked=False` (headless); `engine.state`/`engine.watch_limit` reflect live
state for `/api/status` and `/ready`.

> **Existing-test impact (correcting the over-broad "preserves every engine test" claim):**
> `tests/test_engine.py::test_blocked_when_watch_limit_insufficient_and_enforced` currently does a
> bare `await engine.start()` and asserts `roots_history == []`. Under the new default
> (`park_when_blocked=True`) that would attach a zero-root watcher and idle (not return), so this
> test **must be updated** in sub-plan 03 to call `engine.start(park_when_blocked=False)` (the
> headless contract it actually models). It is the **one** existing engine test that changes; all
> others (running, rebuild-root-swap, aclose) are untouched because `events_bus=None` and the
> gate-ok paths behave exactly as before.
>
> This is the highest-risk change in the phase. Sub-plan 03 drives it with a fake `WatcherBackend`
> + monkeypatched `check_watch_limit`, asserting each of the four transitions above (and headless
> exit-3) before touching real inotify.

---

## J. `run` web wiring — `web/server.py` + `cli.py` (owned by sub-plan 03)

```python
# web/server.py
async def serve_web(
    repo: Repo,
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    session_secret: str,
    stop_event: asyncio.Event | None = None,
) -> int: ...
```

- Creates the `EventsBus`, the `Engine(repo, events_bus=bus)`, calls `bootstrap_password(repo)`
  (off-thread), builds `create_app(repo, engine, bus, session_secret=...)`, and runs **uvicorn +
  `engine.start()` concurrently on one loop**: `engine.start()` as a background task (parks if
  blocked), `uvicorn.Server(...).serve()` until SIGINT/SIGTERM (uvicorn installs the handlers) or
  `stop_event`. On shutdown: `await engine.aclose()` then cancel the start task. Returns `0`.
- **`cli.py`:** `_cmd_run` with `--no-web` is unchanged (`serve_headless`, exit 0/3). **Without**
  `--no-web` it now serves. The session secret reuses the Fernet `/config/secret.key` (PLAN line 237 —
  no new key file), but `_build_repo` currently loads that key only locally and returns just a `Repo`
  (cli.py:55-62). **Refactor `_build_repo` to also surface the key** — e.g. return `tuple[Repo, str]`
  or factor a `_load_key() -> bytes` helper the run path reuses — and note `load_or_create_key`
  returns **`bytes`** (crypto.py) whereas `create_app(..., session_secret: str)` and Starlette's
  `SessionMiddleware.secret_key` want **`str`**, so `.decode()` the urlsafe-base64 key. Then read
  `MSM_HOST`/`MSM_PORT` (defaults `0.0.0.0`/`8080`), `configure_logging()`, and
  `asyncio.run(serve_web(...))`. The Phase-1 "web arrives in Phase 3" stub message and its exit-2 are
  removed.
- `serve_headless` is updated to call `engine.start(park_when_blocked=False)` so its exit-3-on-blocked
  contract holds under the §I gate logic.

### Shared write-core (rule against `/ui`↔`/api` drift)

The `/api/*` JSON routes (sub-plan 02) and the `/ui/*` HTML routes (sub-plan 04) are two presentations
of the **same** mutation. To stop them diverging (e.g. the §D token-required `422` enforced in `/api`
but skipped in `/ui`), the *"validate (incl. `SERVER_TYPE_SPECS` token check) → repo write
(off-thread) → `rebuild_engine`"* core for each entity is a **single shared callable** that both
surfaces invoke; the route handlers only differ in how they parse input (`Form` vs JSON body) and
shape output (HTML partial vs Pydantic model). Define these shared callables in **`web/writes.py`**
(sub-plan 02, lands first) so sub-plan 04 reuses them. Suggested names (the implementer may refine,
but pin them in sub-plan 02 so 04 can call them): `apply_server_create`, `apply_server_update`,
`apply_server_delete`, `apply_folder_create`, `apply_folder_update`, `apply_folder_delete` — each
`async`, each taking `(repo, engine, <ids/validated-model>)`, doing the §D token check, the off-thread
repo write, and `rebuild_engine`, and returning the resulting `Server`/`Folder` (or raising
`HTTPException(422)`).

---

## K. Pages, /ui form handlers & SSE — `web/pages.py` + templates/static (owned by sub-plan 04)

- **Page routes (`require_page_auth`):** `GET /` (dashboard), `GET /servers`, `GET /servers/{id}`,
  `GET /settings`, `GET /events`. Plus unauthenticated `GET /login` and `GET /setup`. Each renders a
  Jinja2 template via `app.state.templates`.
- **htmx form handlers (`/ui/...`, `require_page_auth`):** the HTML forms POST
  `application/x-www-form-urlencoded` to thin `/ui` routes that parse via `Form(...)`, build the same
  write-schemas (§D/§E), call the repo (off-thread) + `rebuild_engine` (§F), and return an **HTML
  partial** (htmx swaps it in). The pure-JSON `/api/*` surface (sub-plan 02) is left untouched for
  programmatic use; `/ui` is the browser's HTML twin sharing the same validation + rebuild helpers.
- **SSE:** `GET /events/stream` (`require_page_auth`) returns
  `StreamingResponse(gen(), media_type="text/event-stream")` where `gen()` first replays
  `bus.recent()` then `async for rec in bus.subscribe()` yields `f"data: {json}\n\n"`, breaking when
  `await request.is_disconnected()`. No `sse-starlette`. The `/events` page opens it with htmx's SSE
  extension (or a 6-line `EventSource` script). **Known race (acceptable):** a record published
  between the `recent()` snapshot and the `subscribe()` queue registration may be missed or shown
  twice; for a best-effort live feed this is fine — do not add locking. Note it in the sub-plan.
- **Static:** `htmx.min.js` (vendored, pinned) + `app.css` under `web/static`, mounted at `/static`
  by `create_app`. Type-specific server form fields are rendered from `SERVER_TYPE_SPECS` +
  `supported_scan_modes` (§D) — templates never branch on a literal server-type name.

---

## Cross-plan invariants (every Phase 3 sub-plan must honor)

1. **Secrets stay in the box.** No token or ciphertext in any response body, URL, log line, SSE
   record, or template — only `has_secret: bool`. Writes take plaintext; reads redact.
2. **Auth-closed by default.** A new route is guarded unless it is on the §B allow-list. Adding a
   route means deciding its guard explicitly.
3. **Off-loop I/O.** `Repo` and `check_watch_limit` always via `asyncio.to_thread`. No blocking call
   in a handler.
4. **Writes rebuild.** Every successful config mutation calls `rebuild_engine` (§F) before responding.
5. **The gate never wedges the UI.** Web serves regardless of engine state; only the engine task is
   gated; `/health` is always `200`; empty config is always ready.
6. **No server-type special-casing.** Per-type behavior comes from `SERVER_TYPE_SPECS` +
   `supported_scan_modes`, declared once (§D). Routers/templates/handlers never branch on a literal
   type name.
7. **Frozen Phase 1/2 contract is consumed, not rewritten.** The only Phase-1 module Phase 3 edits is
   `engine.py` (§G bus hook, §I gate-recovery) — additive: `events_bus=None` reproduces today's
   dispatch exactly, and the gate-ok paths behave as before. **One existing test changes:**
   `tests/test_engine.py::test_blocked_when_watch_limit_insufficient_and_enforced` must pass
   `park_when_blocked=False` (§I) — it models the headless contract. Every other engine/headless test
   is untouched. `cli.py` gains the web `run` path (§J). `db/{repo,schemas}.py` gain folder
   read-one/update (§E). No table schema change, so **no Alembic migration** this phase.
