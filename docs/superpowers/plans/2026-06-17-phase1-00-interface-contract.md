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
  env (`MSM_DB_PATH`, `MSM_SECRET_KEY` / `MSM_SECRET_KEY_FILE`) — see sub-plan 01.
- No blocking I/O on the event loop. DB calls in Phase 1 are sync SQLModel/SQLAlchemy run inside
  `asyncio.to_thread` at the engine boundary (sub-plan 06); repo methods themselves are sync.

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
    server_id: int = Field(foreign_key="server.id", index=True)
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
    folder_id: int = Field(foreign_key="folder.id", index=True)
    extension: str                             # normalized: lowercase, no leading dot
    folder: Folder = Relationship(back_populates="filetypes")

class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)         # e.g. "schema_version", "password_hash"
    value: str
```

**Schema version:** the `Setting` row `key="schema_version"` holds the integer schema version as
a string (starts at `"1"`). Migration step lives in sub-plan 01.

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
    generate+write (chmod 0600). Used by sub-plan 06 at startup."""
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
    secret: str | None         # decrypted
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
def normalize_extension(ext: str) -> str:    # strip leading dot, lowercase, strip whitespace
def normalize_path(path: str) -> str:        # absolute, no trailing slash (except root)
```

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
    current_limit: int
    required: int          # number of directories that will be watched
    ok: bool

def read_max_user_watches(proc_path: str = "/proc/sys/fs/inotify/max_user_watches") -> int: ...
def count_dirs(roots: Iterable[str], ignore_dirs: frozenset[str]) -> int: ...
def check_watch_limit(roots: Iterable[str], ignore_dirs: frozenset[str],
                      headroom: float = 1.2) -> WatchLimitStatus: ...
```

The watcher consumes `FsEvent`/`FsEventType` from sub-plan 02 and `normalize_path` from
`config/defaults.py`. It does **no** extension filtering — that is the pipeline's job.

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
class Engine:
    def __init__(self, repo: Repo, *, watcher: WatcherBackend | None = None) -> None: ...
    async def start(self) -> None:
        # build_runtime_config -> build adapters+clients -> set watcher roots ->
        # consume watcher.events(): for each event, route() -> debouncer.submit() per request
    async def rebuild(self) -> None:
        # rebuild RuntimeConfig from DB; diff watch_paths (set_roots); rebuild adapters;
        # dispatcher.set_adapters(); swap routing snapshot. No restart, no dropped events.
    async def aclose(self) -> None: ...

# cli.py: `run [--no-web]` wires load_or_create_key -> init_db (create_all + migrate) ->
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
   Never in the DB plaintext, never in a logged URL, never in `__repr__` of a model.
4. **Paths** are normalized via `normalize_path` (absolute, no trailing slash) at every boundary
   that stores or compares them (repo create, runtime build, watcher roots, router prefix test).
5. **Prefix match** in `route()` is a path-segment prefix (`/a/b` matches `/a/b/c` but not
   `/a/bc`) — implement with `os.path.commonpath` or a separator-aware check, not raw
   `str.startswith`.
6. **Failure isolation:** a single adapter/server error becomes a `TriggerResult(ok=False)` and
   is logged; it never propagates out of `Dispatcher.dispatch` or aborts the event loop.
