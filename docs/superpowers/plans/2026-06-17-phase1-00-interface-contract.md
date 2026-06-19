# Phase 1 — Shared Interface Contract (FROZEN)

> **Status:** This is not an implementation plan — it is the **frozen vocabulary** that the six
> Phase 1 sub-plans (`01`–`06`) all build against. Types, enum values, method signatures, and
> module paths defined here are authoritative. A sub-plan may **consume** these names but must
> **not redefine or rename** them. If a sub-plan needs to change a contract item, that change is
> made *here first*, then propagated — never forked locally.

**Goal of Phase 1:** configure (DB-seeded) Plex servers + folders and get targeted partial
scans with per-server debounce, headless (`run --no-web`).

**Dependency order of the sub-plans (forward-only):**

```
01 db&crypto ──┬─> 02 types&runtime ──┬─> 03 servers/plex ──┐
               │                       └─> 04 watcher ──────┤
               │                                            ├─> 05 pipeline ─> 06 engine/cli
               └────────────────────────────────────────────┘
```

Each sub-plan is independently testable and mergeable. `05` depends on `02`+`03`; `06` depends
on everything.

---

## 0. Conventions

- Python ≥ 3.14 (single target: `requires-python`, `ruff`/`mypy` targets, CI, and the Docker
  base image all pinned to 3.14), `from __future__ import annotations` at the top of every module.
- Full type hints; `mypy --strict` clean. `ruff` lint+format (line length 100).
- Pure dataclasses for in-memory domain types use `@dataclass(frozen=True, slots=True)`.
- SQLite path: `/config/app.db`. Secret key path: `/config/secret.key`. Both overridable by
  env (`MSM_DB_PATH`, `MSM_SECRET_KEY` / `MSM_SECRET_KEY_FILE`) — see sub-plan 01. These are the
  *only* env vars the app reads in Phase 1. `MSM_INOTIFY_WATCHES` (sidecar/host-only) and
  `MSM_PASSWORD_FILE` (Phase 3 bootstrap) are **not** read by the app here.
- No blocking I/O on the event loop. DB calls in Phase 1 are sync SQLModel/SQLAlchemy run inside
  `asyncio.to_thread` at the engine boundary (sub-plan 06); repo methods themselves are sync.
- **Repo threading model.** Because repo methods run inside `asyncio.to_thread` (a multi-thread
  pool) and SQLite `Session`s are not thread-safe: every `Repo` method opens and closes its own
  `Session` from the injected factory, no `Session` is shared across calls or stored on the repo,
  no `Session` outlives the method that created it, and the SQLite engine is created with
  `connect_args={"check_same_thread": False}` (sub-plan 01).
- **Pure normalizers are a leaf module** (`mediascanmonitor/normalize.py`, owned by sub-plan 01)
  with no intra-package imports, so both `db` and `config` depend *down* onto it (see §1.1). They
  do **not** live in `config/defaults.py`.
- **Plaintext secrets are never reprable.** Any dataclass field or model attribute that holds a
  *decrypted* secret is excluded from `__repr__`/`__str__` (`field(repr=False)` on dataclasses;
  see invariant 3). Ciphertext columns are exempt but discouraged from logging.

---

## 1. Enums (defined in `mediascanmonitor/db/models.py`, imported everywhere)

```python
from enum import Enum

class ServerType(str, Enum):
    webhook = "webhook"
    plex = "plex"
    emby = "emby"
    jellyfin = "jellyfin"
    audiobookshelf = "audiobookshelf"

class ScanMode(str, Enum):
    targeted = "targeted"   # backend scans a specific folder path (Plex ?path=)
    library = "library"     # backend refreshes a whole library id

class DebounceMode(str, Enum):
    off = "off"             # dispatch every matching event
    trailing = "trailing"   # collapse a burst per (server_id, scan_key) after a window
```

---

## 1.1 Pure normalizers (`mediascanmonitor/normalize.py`) — owned by sub-plan 01

Leaf module: **imports nothing from this package**. Both the persistence layer (`db`) and the
config layer (`config`) depend down onto it, so there is no `db ↔ config` cycle.

```python
def normalize_extension(ext: str) -> str:    # strip leading dot(s), lowercase, strip whitespace
def normalize_path(path: str) -> str:        # PURE LEXICAL only (see below)
```

`normalize_path` is **pure and total**: it collapses redundant separators and lexically resolves
`.`/`..`, then strips the trailing slash (except root). It is `os.path.normpath` semantics — it
does **not** read the CWD, does **not** touch the filesystem, and does **not** resolve symlinks
(intentional non-goals; symlink canonicalization, if ever needed, belongs in the watcher). In
particular it does **not** make a relative path absolute — that would require the CWD and make the
function non-deterministic. "Paths must be absolute" is therefore a **validation rule enforced at
the schema boundary** (§4), not a transformation done by the normalizer.

These were previously slated for `config/defaults.py`; they live here so `Repo` (01) can call
`normalize_extension`/`normalize_path` without importing `config`. `config/defaults.py` and the
Pydantic schemas re-use these same functions — there is exactly one implementation of each.

---

## 2. Persistence models (`mediascanmonitor/db/models.py`) — owned by sub-plan 01

SQLModel `table=True` models. Decision: **`FileType` is its own table** (matches the
`Server ─< Folder ─< FileType` domain model and lets cascade-delete be tested explicitly).

```python
class Server(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    type: ServerType
    base_url: str = ""                       # "" for pure webhook with full URL in template
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    secret_encrypted: str | None = None       # Fernet token; never the plaintext
    scan_mode: ScanMode = ScanMode.targeted
    debounce_mode: DebounceMode = DebounceMode.trailing
    debounce_window_seconds: int = 30
    retry_attempts: int = 3                    # total tries (1 = no retry)
    enabled: bool = True
    # webhook-only (unused until Phase 2, defined now to avoid a Phase 2 migration):
    webhook_method: str | None = None
    webhook_headers_json: str | None = None
    webhook_body_template: str | None = None
    folders: list["Folder"] = Relationship(
        back_populates="server",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

class Folder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", ondelete="CASCADE", index=True)
    path: str                                  # host path watched, e.g. /data/media/tvseries
    library_id: str | None = None              # backend section/library id; None for webhook
    enabled: bool = True
    server: Server = Relationship(back_populates="folders")
    filetypes: list["FileType"] = Relationship(
        back_populates="folder",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

class FileType(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    folder_id: int = Field(foreign_key="folder.id", ondelete="CASCADE", index=True)
    extension: str                             # normalized: lowercase, no leading dot
    folder: Folder = Relationship(back_populates="filetypes")

class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)         # e.g. "password_hash", "inotify_gate"
    value: str
```

**Foreign keys are enforced.** SQLite ignores `FOREIGN KEY` constraints unless
`PRAGMA foreign_keys=ON` is set **per connection**. Sub-plan 01's engine factory registers a
`connect` listener that runs it, so (a) inserting a `Folder`/`FileType` with a dangling parent id
raises `IntegrityError` instead of silently orphaning, and (b) `ondelete="CASCADE"` gives a
DB-level cascade as defense-in-depth alongside the ORM `cascade="all, delete-orphan"` (the ORM
deletes children first, so the two never conflict).

**Schema migrations: Alembic.** Schema evolution goes through **Alembic** (rule 7 — never
silently break `app.db`), not `create_all`. `init_db` runs `alembic upgrade head` at startup;
Alembic's own `alembic_version` table is the source of truth for the applied revision. The
initial revision (`0001`) creates the four tables above. SQLite's limited `ALTER TABLE` is
handled with Alembic **batch mode** (`render_as_batch=True`). The `Setting` table stays (it holds
`password_hash`, `inotify_gate`, …) but there is **no** `schema_version` row — Alembic owns
versioning. Scaffolding + the initial revision live in sub-plan 01.

**inotify gate policy:** the `Setting` row `key="inotify_gate"` holds `"enforce"` (default) or
`"off"` — the only in-app inotify knob (PLAN.md → Deployment, "the app measures, it doesn't
store a target"). It is a *policy*, never a watch-count number. The engine consults it (§10); the
Phase 3 UI toggles it. There is **no seeding step** — an absent row reads as `None` and the
engine treats that as `"enforce"` (`get_setting("inotify_gate") or "enforce"`), so a fresh
install enforces by default. Phase 1 reads it but ships no UI.

---

## 3. Secret crypto (`mediascanmonitor/db/crypto.py`) — owned by sub-plan 01

```python
class SecretBox:
    def __init__(self, key: bytes) -> None: ...
    def encrypt(self, plaintext: str) -> str: ...     # -> Fernet token (str)
    def decrypt(self, token: str) -> str: ...         # raises SecretDecryptError on failure

class SecretDecryptError(Exception): ...

def load_or_create_key(path: Path, env_key: str | None = None) -> bytes:
    """Return a urlsafe-base64 Fernet key. Precedence: env_key > file at path >
    generate+atomically-create the file with mode 0600 (no write-then-chmod window).
    env_key and file contents are stripped of surrounding whitespace. Used by sub-plan 06
    at startup."""
```

---

## 4. Repository (`mediascanmonitor/db/repo.py`) — owned by sub-plan 01

Sync class wrapping a `Session` factory. The `SecretBox` is injected so the repo stores
ciphertext and never leaks plaintext into the DB.

```python
class Repo:
    def __init__(self, session_factory: Callable[[], Session], box: SecretBox) -> None: ...

    # servers ----------------------------------------------------------------
    def create_server(self, data: ServerCreate) -> Server: ...   # encrypts data.secret
    def get_server(self, server_id: int) -> Server | None: ...
    def list_servers(self, *, enabled_only: bool = False) -> list[Server]: ...
    def update_server(self, server_id: int, data: ServerUpdate) -> Server: ...
    def delete_server(self, server_id: int) -> None: ...         # cascades folders+filetypes

    # folders ----------------------------------------------------------------
    def create_folder(self, server_id: int, data: FolderCreate) -> Folder: ...
    def list_folders(self, server_id: int) -> list[Folder]: ...
    def delete_folder(self, folder_id: int) -> None: ...

    # filetypes --------------------------------------------------------------
    def set_filetypes(self, folder_id: int, extensions: list[str]) -> list[FileType]: ...
    # replaces the folder's extension set wholesale (normalizes via normalize_extension)

    # secrets / settings -----------------------------------------------------
    def resolve_secret(self, server: Server) -> str | None: ...  # decrypts or None
    def get_setting(self, key: str) -> str | None: ...
    def set_setting(self, key: str, value: str) -> None: ...
```

`ServerCreate`, `ServerUpdate`, `FolderCreate` are Pydantic models defined in sub-plan 01
(`db/schemas.py`). `ServerCreate.secret` / `ServerUpdate.secret` are plaintext-in, encrypted by
the repo. The API-facing redaction read-models are Phase 3 — **not** in scope here, but the repo
must never return plaintext except via `resolve_secret`.

**Normalization chokepoint:** path/extension normalization and validation are performed by
**field validators on the Pydantic schemas**, not ad-hoc in each repo method:
- `FolderCreate.path` → `normalize_path(...)`, then **assert the result is absolute**
  (`os.path.isabs`); a relative path raises a `ValidationError` (fail-fast — the Phase 3 web
  layer surfaces it as a 422). This is where the "paths are absolute" rule lives (§1.1).
- `FolderCreate.extensions` → `normalize_extension` each element, drop empties, **dedupe**
  (preserving order) so the DB never gets duplicate `FileType` rows.

Because the validated `FolderCreate` is already normalized, `Repo.create_folder` **trusts it**
and stores the values as-is — it does not re-normalize. The typed boundary is therefore
truthful: holding a `FolderCreate` means holding normalized, absolute, deduped data. The single
exception is `set_filetypes`, which takes a raw `list[str]` (no schema): it normalizes + dedupes
inline with the same leaf-module `normalize_extension`. Any value entering the DB thus passes
through exactly one normalizer call site, so invariant 4 holds by construction.

**Error model:** mutations targeting a missing id raise `KeyError` (`update_server`,
`set_filetypes`); `delete_server`/`delete_folder` are **idempotent** (no-op on a missing id, no
raise); `create_server` surfaces the underlying `IntegrityError` on a duplicate `name`, and
`create_folder` surfaces `IntegrityError` on a dangling `server_id` (FK enforcement, §2).
Callers (sub-plans 02/03/06, Phase 3 web) catch accordingly.

**Loading model (detached-instance safety):** repo methods return ORM instances after their
session closes (sessions use `expire_on_commit=False`, so already-loaded **column** attributes
stay readable). Relationships are a different matter: `list_servers` returns `Server` rows
**without** `folders` loaded — accessing `server.folders` on the result raises
`DetachedInstanceError`. Consumers walk children via `list_folders(server_id)` instead, which
**force-loads** each `Folder.filetypes` while its session is open. `build_runtime_config` (02) is
built on exactly this: servers first, then `list_folders` per server, never touching
`Server.folders`.

**Assembly (`mediascanmonitor/db/session.py`, sub-plan 01).** Two distinct helpers — do not
conflate their return types:

```python
def init_db(db_path: str | os.PathLike[str]) -> Engine:   # runs `alembic upgrade head`; returns the ENGINE
def session_factory(engine: Engine) -> Callable[[], Session]: ...   # zero-arg Session producer
```

A `Repo` is therefore built as `Repo(session_factory(init_db(db_path)), box)` (sub-plan 06's
`_build_repo`). `init_db` migrates the DB to `head` (Alembic, §2) and returns the Engine — it does
**not** return a factory and does **not** call `create_all`.

---

## 5. Domain event/request types (`mediascanmonitor/pipeline/events.py`) — owned by sub-plan 02

```python
class FsEventType(str, Enum):
    created = "created"        # inotify CREATE
    moved_to = "moved_to"      # inotify MOVED_TO
    deleted = "deleted"        # inotify DELETE
    moved_from = "moved_from"  # inotify MOVED_FROM

@dataclass(frozen=True, slots=True)
class FsEvent:
    path: str                  # absolute path of the changed entry
    event_type: FsEventType
    is_dir: bool

@dataclass(frozen=True, slots=True)
class ScanRequest:
    server_id: int
    server_name: str
    scan_mode: ScanMode
    scan_path: str | None      # host path to scan (targeted); None for library mode
    library_id: str | None     # backend library/section id
    scan_key: str              # debounce key: scan_path (targeted) or f"lib:{library_id}" (library)
    # context (used by webhook templating in Phase 2; carried now):
    event_type: FsEventType
    file_path: str             # the originating absolute file path
    top_folder: str | None     # first path segment under the folder root (targeted), else None
```

---

## 6. Runtime config (`mediascanmonitor/config/runtime.py`) — owned by sub-plan 02

Immutable snapshot built from the DB; the router/dispatcher read it. `secret` is decrypted
in-memory here (adapters receive plaintext token).

```python
@dataclass(frozen=True, slots=True)
class ServerRuntime:
    server_id: int
    name: str
    type: ServerType
    base_url: str
    verify_tls: bool
    timeout_seconds: float
    secret: str | None = field(repr=False)   # decrypted plaintext; excluded from repr (invariant 3)
    scan_mode: ScanMode
    debounce_mode: DebounceMode
    debounce_window_seconds: int
    retry_attempts: int
    webhook_method: str | None
    webhook_headers_json: str | None
    webhook_body_template: str | None

@dataclass(frozen=True, slots=True)
class FolderRoute:
    server_id: int
    server_name: str
    path: str                  # watched folder root (normalized, no trailing slash)
    extensions: frozenset[str] # normalized; EMPTY SET MEANS "match all extensions"
    library_id: str | None
    scan_mode: ScanMode

@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    watch_paths: frozenset[str]          # dedup union of enabled folder paths
    routes: tuple[FolderRoute, ...]      # one per enabled (server, folder)
    servers: dict[int, ServerRuntime]    # by server_id (enabled only)
    ignore_dirs: frozenset[str]

def build_runtime_config(repo: Repo) -> RuntimeConfig:
    """Read enabled servers/folders/filetypes from the DB, decrypt secrets, and assemble the
    immutable snapshot. Disabled servers and their folders are excluded."""
```

`mediascanmonitor/config/defaults.py` (sub-plan 02) provides:

```python
IGNORE_DIRS: frozenset[str]                  # {"@eaDir", "#snapshot", "#recycle", "@tmp"}
EXTENSION_PRESETS: dict[str, tuple[str, ...]]  # "video", "subtitles", "audio"
DEFAULT_DEBOUNCE_WINDOW_SECONDS: int = 30
DEFAULT_DEBOUNCE_BY_TYPE: dict[ServerType, DebounceMode]  # plex/emby/jf/abs=trailing, webhook=off
```

`normalize_extension` / `normalize_path` are **not** here — they live in the leaf module
`mediascanmonitor/normalize.py` (§1.1). `config/defaults.py` imports them if it needs them.

---

## 7. Server adapters (`mediascanmonitor/servers/`) — owned by sub-plan 03

```python
# servers/base.py
@dataclass(frozen=True, slots=True)
class TriggerResult:
    ok: bool
    status_code: int | None
    detail: str

@dataclass(frozen=True, slots=True)
class TestResult:
    ok: bool
    detail: str

class ServerAdapter(ABC):
    server_type: ClassVar[ServerType]
    supported_scan_modes: ClassVar[frozenset[ScanMode]]

    def __init__(self, server: ServerRuntime, client: httpx.AsyncClient) -> None:
        self.server = server
        self.client = client

    @abstractmethod
    async def trigger(self, req: ScanRequest) -> TriggerResult: ...

    @abstractmethod
    async def test(self) -> TestResult: ...   # auth + reachability only
```

```python
# servers/registry.py
def register(cls: type[ServerAdapter]) -> type[ServerAdapter]: ...   # class decorator
def get_adapter_class(server_type: ServerType) -> type[ServerAdapter]: ...
def create_adapter(server: ServerRuntime, client: httpx.AsyncClient) -> ServerAdapter: ...
```

```python
# servers/http.py
def build_client(*, verify_tls: bool, timeout_seconds: float) -> httpx.AsyncClient: ...
async def request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, *, attempts: int, **kwargs: Any,
) -> httpx.Response: ...   # tenacity: retry on httpx.TransportError + 5xx; exp backoff
```

**Plex adapter (`servers/plex.py`) contract** (the one concrete adapter in Phase 1):
- `server_type = ServerType.plex`, `supported_scan_modes = frozenset({targeted, library})`.
- `trigger`: `GET {base_url}/library/sections/{library_id}/refresh`; targeted adds
  `?path={quote(scan_path)}`. Header `X-Plex-Token: {secret}`. 2xx ⇒ `TriggerResult(ok=True, ...)`.
- `test`: `GET {base_url}/identity` with the token; 2xx ⇒ ok.
- Token must never appear in any logged URL (pass as header, never query for logging).

---

## 8. Watcher (`mediascanmonitor/watcher/`) — owned by sub-plan 04

```python
# watcher/base.py
class WatcherBackend(Protocol):
    def set_roots(self, roots: set[str]) -> None: ...       # recursive; idempotent diff
    def events(self) -> AsyncIterator[FsEvent]: ...          # async generator
    async def aclose(self) -> None: ...

# watcher/inotify_backend.py
class InotifyBackend:                                        # implements WatcherBackend
    def __init__(self, ignore_dirs: frozenset[str]) -> None: ...
    # adds a watch per directory under each root; on subdir create, adds a watch and rescans
    # its existing contents (closes the attach-window race), emitting synthetic created events.

# watcher/watch_limit.py
@dataclass(frozen=True, slots=True)
class WatchLimitStatus:
    current: int           # live value read from /proc (the kernel ceiling)
    dirs: int              # raw count of directories that will be watched (one watch each)
    needed: int            # gate threshold = ceil(dirs * headroom)
    recommended: int       # kernel ceiling to advise the user = ceil(needed * headroom)
    ok: bool               # current >= needed

def read_max_user_watches(proc_path: str = "/proc/sys/fs/inotify/max_user_watches") -> int: ...
def count_dirs(roots: Iterable[str], ignore_dirs: frozenset[str]) -> int: ...
def check_watch_limit(roots: Iterable[str], ignore_dirs: frozenset[str],
                      headroom: float = 1.2) -> WatchLimitStatus: ...
```

**The app measures `needed`; it never stores a "required" target.** All three numbers are
*measurements* — `dirs`/`needed` derived from the current config, `current` read live from
`/proc` — not user-supplied settings. The gate is purely `current >= needed`; there is no
threshold knob in the loop. `MSM_INOTIFY_WATCHES` (PLAN.md → Deployment) is a **sidecar/host**
env var that sets the kernel *ceiling* before the app starts — it is **never read by the app**.
The only in-app knob is the `inotify_gate` policy `Setting` (§2): `enforce` (default) lets the
gate block the engine; `off` makes the engine attach regardless (the Bash `=0` escape hatch).

The watcher consumes `FsEvent`/`FsEventType` from sub-plan 02 and `normalize_path` from
`mediascanmonitor/normalize.py` (§1.1). It does **no** extension filtering — that is the
pipeline's job.

**Resilience (never fail silently — rule 8):**
- **Queue overflow.** The kernel drops events with `IN_Q_OVERFLOW` when the inotify queue is
  exceeded (e.g. a NAS bulk import). The backend must detect it, log a warning, and **resync**:
  re-attach watches across all roots (a subdir whose `CREATE` was dropped is otherwise unwatched)
  and re-emit their contents as synthetic `created` events. This is a *bounded* recovery — the
  per-`scan_key` debouncer collapses the burst — and it guarantees a dropped event never means a
  permanently missed scan. Overflow is handled *before* the normal `event.path is None` skip.
- **Runtime `add_watch` failure.** Adding a watch can fail at runtime (kernel limit hit / `ENOSPC`)
  even though the startup gate passed, because watches grow as directories appear. Such failures
  are caught, logged, and skipped (that directory is simply unwatched) — never propagated out of
  `events()`/`set_roots`. The dashboard's `check_watch_limit` surfaces the shortfall.

---

## 9. Pipeline (`mediascanmonitor/pipeline/`) — owned by sub-plan 05

```python
# pipeline/filters.py
def is_ignored(path: str, ignore_dirs: frozenset[str]) -> bool: ...    # any path segment matches
def extension_matches(path: str, extensions: frozenset[str]) -> bool:  # empty set => True (all)

# pipeline/router.py
def compute_scan_path(folder_root: str, file_path: str) -> tuple[str, str | None]:
    """Return (scan_path, top_folder): folder_root joined with the first path segment of
    file_path below it. If file sits directly in folder_root, top_folder is None and
    scan_path == folder_root."""

def route(event: FsEvent, config: RuntimeConfig) -> list[ScanRequest]:
    """For each FolderRoute whose path is a prefix of event.path, that is not ignored, and whose
    extensions match: build one ScanRequest (scan_mode/scan_key per server)."""

# pipeline/debounce.py
class Debouncer:
    def __init__(
        self,
        dispatch: Callable[[ScanRequest], Awaitable[None]],
        servers: dict[int, ServerRuntime],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,   # injectable for fake clock
    ) -> None: ...
    async def submit(self, req: ScanRequest) -> None:
        # server.debounce_mode == off  -> await dispatch(req) immediately
        # == trailing -> coalesce per (server_id, scan_key); (re)arm timer for window seconds
    def update_servers(self, servers: dict[int, ServerRuntime]) -> None:
        # rebuild swap-in-place (see §10): replace the server map WITHOUT discarding the
        # instance or its pending timers. A pending (server_id, scan_key) whose server is gone
        # from `servers` is cancelled (no dispatch — the server is disabled/deleted). A pending
        # timer whose server survives keeps running; its window length is read from the new map
        # when it next (re)arms, not retroactively.
    async def aclose(self) -> None: ...    # cancel/flush pending timers

# pipeline/dispatcher.py
class Dispatcher:
    def __init__(self, adapters: dict[int, ServerAdapter]) -> None: ...
    async def dispatch(self, req: ScanRequest) -> TriggerResult:
        # look up adapter by req.server_id; call trigger; isolate exceptions into
        # TriggerResult(ok=False, ...); log + (Phase 4) metric. One bad server never raises.
    def set_adapters(self, adapters: dict[int, ServerAdapter]) -> None: ...  # for rebuild swap
```

---

## 10. Engine + CLI + logging (`mediascanmonitor/engine.py`, `observ/logging.py`, `cli.py`) — owned by sub-plan 06

```python
# observ/logging.py
def configure_logging(*, json_logs: bool = True, level: str = "INFO") -> None: ...  # structlog

# engine.py
class EngineState(str, Enum):
    starting = "starting"
    running = "running"
    blocked = "blocked"        # inotify gate not satisfied; watcher not attached, web stays up
    stopped = "stopped"

class Engine:
    def __init__(self, repo: Repo, *, watcher: WatcherBackend | None = None) -> None: ...
    state: EngineState
    watch_limit: WatchLimitStatus | None   # last gate measurement, surfaced to /ready + dashboard
    async def start(self) -> None:
        # build_runtime_config -> _attach():
        #   gate = check_watch_limit(cfg.watch_paths, cfg.ignore_dirs)
        #   if inotify_gate == "enforce" and not gate.ok and cfg.watch_paths:
        #       state = blocked; DO NOT attach the watcher; return (retryable — NOT a process exit)
        #   else: build adapters+clients -> set watcher roots -> state = running ->
        #         consume watcher.events(): each event route() -> debouncer.submit() per request
        # No-deadlock rule (PLAN.md): the gate gates only this engine task; it never exits the
        # process or blocks the (Phase 3) web layer. An empty config needs 0 watches => never
        # blocked. Phase 1 headless `--no-web` is the one exception: when blocked with no UI to
        # recover through, it logs the remediation line and exits non-zero (Bash-style), unless
        # inotify_gate == "off".
    async def rebuild(self) -> None:
        # 1. cfg = await to_thread(build_runtime_config, repo)   # may block; do it off-loop first
        # 2. build new adapters+clients from cfg.servers; close clients dropped since last cfg
        # 3. SWAP (synchronous, no await between these — the consume loop cannot interleave):
        #       self._config = cfg                       # routing snapshot
        #       dispatcher.set_adapters(new_adapters)    # keyed by server_id
        #       debouncer.update_servers(cfg.servers)    # keyed by server_id, in place
        # 4. re-evaluate the inotify gate against cfg (it changed the watch set, and a config
        #    edit is exactly when the host fix / inotify_gate flip lands). Transition
        #    blocked<->running and only set_roots when attaching; recovery needs no restart.
        # 5. self.watcher.set_roots(cfg.watch_paths)     # diff add/remove watches (when running)
        # No restart, no dropped events. The three server-keyed structures
        # (config.servers, dispatcher adapters, debouncer servers) are all derived from the SAME
        # cfg.servers and swapped in one uninterrupted step, so they can never be observed
        # inconsistent by an in-flight event (invariant 7).
    async def aclose(self) -> None: ...

# cli.py: `run [--no-web]` wires load_or_create_key -> init_db (alembic upgrade head) ->
# Repo -> Engine.start(); --no-web is the only mode implemented in Phase 1.
```

The watcher is injectable into `Engine` so non-Linux dev/tests can pass a fake backend.

---

## Cross-plan invariants (every sub-plan must honor)

1. **Empty extension set means "all extensions"** — in `FolderRoute.extensions`, `set_filetypes`
   with `[]`, and `extension_matches`.
2. **`scan_key`** = `scan_path` for `targeted`, `f"lib:{library_id}"` for `library`. Set in
   `route()`, consumed by `Debouncer`.
3. **Secrets** are plaintext only inside `ServerRuntime.secret` (in memory) and adapter headers.
   Never in the DB plaintext, never in a logged URL, never in a `__repr__`. Enforced concretely:
   `ServerRuntime.secret` uses `field(repr=False)` (§6), and no log statement formats a whole
   `ServerRuntime`/adapter that would expand the token.
4. **Paths** are normalized to a canonical lexical form (no redundant separators, `.`/`..`
   resolved, no trailing slash except root) via `normalize_path` from the leaf module
   `mediascanmonitor/normalize.py` (§1.1). The normalizer does **not** make paths absolute;
   absoluteness is asserted at the schema boundary (§4). Normalization happens at a **single
   chokepoint per boundary**: the Pydantic schema validators on the way *into* the DB (§4), and
   `build_runtime_config` / watcher `set_roots` on the way *out*. The router's prefix test then
   compares already-normalized paths; it does not re-normalize.
5. **Prefix match** in `route()` is a path-segment prefix (`/a/b` matches `/a/b/c` but not
   `/a/bc`) — implement with `os.path.commonpath` or a separator-aware check, not raw
   `str.startswith`.
6. **Failure isolation:** a single adapter/server error becomes a `TriggerResult(ok=False)` and
   is logged; it never propagates out of `Dispatcher.dispatch` or aborts the event loop.
7. **Single source of server state across rebuild.** `RuntimeConfig.servers`, the dispatcher's
   adapter map, and the debouncer's server map are all keyed by `server_id` and all derived from
   one freshly built `cfg.servers`. `Engine.rebuild()` swaps all three in one synchronous step
   with no `await` between them (§10), so no in-flight event can observe an adapter without its
   server runtime, or vice versa. The three are never edited independently.
