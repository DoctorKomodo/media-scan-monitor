# Phase 1 — Sub-plan 02: Domain Types & Config/Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the frozen domain event/request types (`pipeline/events.py`), the pure config defaults (`config/defaults.py` — constants only), and the immutable runtime-config snapshot + builder (`config/runtime.py`) exactly as specified in the Phase 1 interface contract sections 5 and 6.

**Architecture:** Three small, single-responsibility modules. `config/defaults.py` is pure constants (ignore-dirs, extension presets, debounce defaults); the path/extension **normalizers are NOT here** — they live in the leaf module `mediascanmonitor/normalize.py` owned by sub-plan 01 (contract §1.1), and this sub-plan imports them. `pipeline/events.py` defines frozen slotted dataclasses for the event pipeline. `config/runtime.py` defines the frozen runtime dataclasses and `build_runtime_config(repo)`, which reads enabled servers/folders/filetypes from the `Repo`, decrypts secrets via `repo.resolve_secret`, normalizes paths, and assembles an immutable `RuntimeConfig`.

**Tech Stack:** Python ≥ 3.14, stdlib `dataclasses`/`enum`/`os`, `pydantic==2.13.4`/`sqlmodel==0.0.38` (only via the contract's `db/models.py` enums + table models, owned by sub-plan 01), `cryptography==49.0.0` (only indirectly, via the repo's `resolve_secret`). Tests use `pytest==9.1.0`. `mypy --strict` clean, `ruff` clean, line length 100, `from __future__ import annotations` in every module.

---

## Contract authority (consume verbatim — do NOT redefine)

This sub-plan **owns** (defines) exactly these names, copied verbatim from the FROZEN contract
(`docs/superpowers/plans/2026-06-17-phase1-00-interface-contract.md`):

- Section 5 — `pipeline/events.py`: `FsEventType`, `FsEvent`, `ScanRequest`.
- Section 6 — `config/defaults.py`: `IGNORE_DIRS`, `EXTENSION_PRESETS`,
  `DEFAULT_DEBOUNCE_WINDOW_SECONDS`, `DEFAULT_DEBOUNCE_BY_TYPE` (constants only).
- Section 6 — `config/runtime.py`: `ServerRuntime`, `FolderRoute`, `RuntimeConfig`,
  `build_runtime_config`.

This sub-plan **consumes** (imports, never redefines) these names owned by **sub-plan 01**:

- Section 1.1 normalizers from `mediascanmonitor/normalize.py`: `normalize_extension`,
  `normalize_path` (leaf module; `config/defaults.py` and `config/runtime.py` import them).
- Section 1 enums from `mediascanmonitor/db/models.py`: `ServerType`, `ScanMode`, `DebounceMode`.
- Section 2 table models from `mediascanmonitor/db/models.py`: `Server`, `Folder`, `FileType`.
- Section 4 repository from `mediascanmonitor/db/repo.py`: `Repo` (only its method signatures
  `list_servers(*, enabled_only=False)`, `list_folders(server_id)`, `resolve_secret(server)`).

**Dependency note:** the contract's forward-only order is `01 → 02`. Sub-plan 01 (DB & crypto)
must be merged before this sub-plan, because every module here imports the enums/models from
`db/models.py`, `config/defaults.py` + `config/runtime.py` import the normalizers from
`mediascanmonitor/normalize.py`, and `config/runtime.py` type-references `db/repo.py:Repo`. Do
not start this sub-plan until `mediascanmonitor/normalize.py` (section 1.1 normalizers),
`mediascanmonitor/db/models.py` (section-1 enums + section-2 models), and
`mediascanmonitor/db/repo.py` (section-4 `Repo`) exist.

### Cross-plan invariants honored here

- **Invariant 1 — empty extension set means "all":** a folder with no `FileType` rows produces
  `FolderRoute.extensions == frozenset()`. Encoded by Task 5's "empty → all" test; the *matching*
  semantics live in sub-plan 05.
- **Invariant 3 — secrets:** plaintext appears only inside `ServerRuntime.secret` (in memory),
  sourced from `repo.resolve_secret`. Never logged, never stored.
- **Invariant 4 — paths normalized:** every folder path is passed through `normalize_path`
  before it enters `watch_paths` or a `FolderRoute.path`.

### How tests obtain a `Repo` (documented choice)

`build_runtime_config` is typed `def build_runtime_config(repo: Repo) -> RuntimeConfig`. Building a
*real* in-memory SQLite `Repo` in these tests would require sub-plan 01's `ServerCreate` /
`FolderCreate` Pydantic schemas, whose field names are **not frozen** in the contract (they are
"defined in sub-plan 01"). To keep this sub-plan independently testable and not coupled to
sub-plan 01's *unfrozen* schema details, the tests use a **typed structural stub** `FakeRepo`
that:

- exposes only the three methods `build_runtime_config` calls (`list_servers`, `list_folders`,
  `resolve_secret`),
- returns real, transient (never session-added) `Server` / `Folder` / `FileType` model
  instances from section 2 (these classes *are* frozen), with `Folder.filetypes` populated
  directly as a plain list, and
- has `resolve_secret` return the plaintext token from a dict — exactly what the real repo's
  `resolve_secret` returns after Fernet decryption.

`FakeRepo` is passed to the builder via `cast("Repo", fake)` (string forward-ref so no runtime
import of `Repo` is needed). The Fernet decrypt round-trip itself is sub-plan 01's tested
concern; at this boundary `build_runtime_config` only needs to surface `resolve_secret`'s output
into `ServerRuntime.secret`, which the stub verifies faithfully.

---

## File structure

| File | Responsibility | Created by |
|---|---|---|
| `mediascanmonitor/config/defaults.py` | Ignore-dir set, extension presets, debounce defaults (pure constants; normalizers live in `normalize.py`, sub-plan 01) | Task 1 |
| `mediascanmonitor/pipeline/events.py` | `FsEventType`, `FsEvent`, `ScanRequest` frozen slotted dataclasses | Task 2 |
| `mediascanmonitor/config/runtime.py` | `ServerRuntime`, `FolderRoute`, `RuntimeConfig` frozen dataclasses + `build_runtime_config` | Tasks 3–4 |
| `tests/config/__init__.py` | test-package marker | Task 1 |
| `tests/config/test_defaults.py` | constants tests | Task 1 |
| `tests/pipeline/__init__.py` | test-package marker | Task 2 |
| `tests/pipeline/test_events.py` | event/request dataclass tests | Task 2 |
| `tests/config/test_runtime.py` | runtime dataclass + `build_runtime_config` tests (incl. `FakeRepo`) | Tasks 3–5 |

The package directories `mediascanmonitor/config/`, `mediascanmonitor/pipeline/`, and the test
roots already exist (Phase 0 skeleton); `tests/config/` and `tests/pipeline/` are new.

---

## Task 1: `config/defaults.py` — pure constants

Constants only (ignore-dirs, extension presets, debounce defaults). The path/extension
normalizers are **not** here — they live in `mediascanmonitor/normalize.py` (sub-plan 01,
already merged) and are imported where needed.

**Files:**
- Create: `mediascanmonitor/config/defaults.py`
- Create: `tests/config/__init__.py`
- Create: `tests/config/test_defaults.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/config/__init__.py` with a single line:

```python
"""Tests for mediascanmonitor.config."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/config/test_defaults.py`:

```python
"""Tests for config/defaults.py — pure constants."""

from __future__ import annotations

from mediascanmonitor.config.defaults import (
    DEFAULT_DEBOUNCE_BY_TYPE,
    DEFAULT_DEBOUNCE_WINDOW_SECONDS,
    EXTENSION_PRESETS,
    IGNORE_DIRS,
)
from mediascanmonitor.db.models import DebounceMode, ServerType


def test_ignore_dirs_contains_synology_system_folders() -> None:
    assert IGNORE_DIRS == frozenset({"@eaDir", "#snapshot", "#recycle", "@tmp"})


def test_ignore_dirs_is_frozenset() -> None:
    assert isinstance(IGNORE_DIRS, frozenset)


def test_default_debounce_window_seconds() -> None:
    assert DEFAULT_DEBOUNCE_WINDOW_SECONDS == 30


def test_extension_presets_have_expected_keys() -> None:
    assert set(EXTENSION_PRESETS) == {"video", "subtitles", "audio"}


def test_extension_presets_are_normalized_tuples() -> None:
    for exts in EXTENSION_PRESETS.values():
        assert isinstance(exts, tuple)
        for ext in exts:
            # Already normalized: lowercase, no leading dot, no whitespace.
            assert ext == ext.strip().lstrip(".").lower()
    assert "mkv" in EXTENSION_PRESETS["video"]
    assert "srt" in EXTENSION_PRESETS["subtitles"]
    assert "mp3" in EXTENSION_PRESETS["audio"]


def test_default_debounce_by_type_covers_every_server_type() -> None:
    assert set(DEFAULT_DEBOUNCE_BY_TYPE) == set(ServerType)


def test_default_debounce_by_type_values() -> None:
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.webhook] == DebounceMode.off
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.plex] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.emby] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.jellyfin] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.audiobookshelf] == DebounceMode.trailing
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/config/test_defaults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.config.defaults'`.

- [ ] **Step 4: Write minimal implementation**

Create `mediascanmonitor/config/defaults.py`:

```python
"""Pure config defaults: ignore-dirs, extension presets, per-type debounce policy.

Constants only — the path/extension normalizers live in the leaf module
`mediascanmonitor.normalize` (sub-plan 01). This module imports the enums from
`db.models` and nothing else from the package; keep it import-light and pure.
"""

from __future__ import annotations

from mediascanmonitor.db.models import DebounceMode, ServerType

# Synology (and similar NAS) system directories that must never trigger a scan.
IGNORE_DIRS: frozenset[str] = frozenset({"@eaDir", "#snapshot", "#recycle", "@tmp"})

# Suggested, already-normalized extension sets offered as UI presets (Phase 3).
EXTENSION_PRESETS: dict[str, tuple[str, ...]] = {
    "video": ("mkv", "mp4", "avi", "ts", "m4v", "mov", "wmv", "flv", "webm"),
    "subtitles": ("srt", "smi", "ssa", "ass", "sub", "idx", "sup", "vtt"),
    "audio": ("mp3", "flac", "m4b", "m4a", "aac", "ogg", "opus", "wav"),
}

# Default trailing-debounce window (seconds) when a server uses trailing mode.
DEFAULT_DEBOUNCE_WINDOW_SECONDS: int = 30

# Per-server-type default debounce policy. Media servers collapse bursts (trailing);
# generic webhooks want every event (off). Overridable per server in the UI (Phase 3).
DEFAULT_DEBOUNCE_BY_TYPE: dict[ServerType, DebounceMode] = {
    ServerType.webhook: DebounceMode.off,
    ServerType.plex: DebounceMode.trailing,
    ServerType.emby: DebounceMode.trailing,
    ServerType.jellyfin: DebounceMode.trailing,
    ServerType.audiobookshelf: DebounceMode.trailing,
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/config/test_defaults.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/config/defaults.py tests/config/__init__.py tests/config/test_defaults.py
git commit -m "feat(config): add ignore-dirs, extension presets, debounce defaults"
```

---

## Task 2: `pipeline/events.py` — `FsEventType`, `FsEvent`, `ScanRequest`

**Files:**
- Create: `mediascanmonitor/pipeline/events.py`
- Create: `tests/pipeline/__init__.py`
- Create: `tests/pipeline/test_events.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/pipeline/__init__.py`:

```python
"""Tests for mediascanmonitor.pipeline."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/pipeline/test_events.py`:

```python
"""Tests for pipeline/events.py — frozen slotted domain types."""

from __future__ import annotations

import dataclasses

import pytest
from mediascanmonitor.db.models import ScanMode
from mediascanmonitor.pipeline.events import FsEvent, FsEventType, ScanRequest


def test_fs_event_type_values() -> None:
    assert FsEventType.created.value == "created"
    assert FsEventType.moved_to.value == "moved_to"
    assert FsEventType.deleted.value == "deleted"
    assert FsEventType.moved_from.value == "moved_from"
    assert set(FsEventType) == {
        FsEventType.created,
        FsEventType.moved_to,
        FsEventType.deleted,
        FsEventType.moved_from,
    }


def test_fs_event_type_is_str_enum() -> None:
    # str-Enum so it serializes/compares as its value.
    assert FsEventType.created == "created"


def test_fs_event_fields() -> None:
    ev = FsEvent(path="/data/media/tv/Show/ep.mkv", event_type=FsEventType.created, is_dir=False)
    assert ev.path == "/data/media/tv/Show/ep.mkv"
    assert ev.event_type is FsEventType.created
    assert ev.is_dir is False


def test_fs_event_is_frozen() -> None:
    ev = FsEvent(path="/x", event_type=FsEventType.deleted, is_dir=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.path = "/y"  # type: ignore[misc]


def test_fs_event_is_slotted() -> None:
    ev = FsEvent(path="/x", event_type=FsEventType.deleted, is_dir=True)
    assert not hasattr(ev, "__dict__")


def test_scan_request_fields() -> None:
    req = ScanRequest(
        server_id=1,
        server_name="plex-main",
        scan_mode=ScanMode.targeted,
        scan_path="/data/media/tv/Shoresy",
        library_id="2",
        scan_key="/data/media/tv/Shoresy",
        event_type=FsEventType.created,
        file_path="/data/media/tv/Shoresy/S01E01.mkv",
        top_folder="Shoresy",
    )
    assert req.server_id == 1
    assert req.server_name == "plex-main"
    assert req.scan_mode is ScanMode.targeted
    assert req.scan_path == "/data/media/tv/Shoresy"
    assert req.library_id == "2"
    assert req.scan_key == "/data/media/tv/Shoresy"
    assert req.event_type is FsEventType.created
    assert req.file_path == "/data/media/tv/Shoresy/S01E01.mkv"
    assert req.top_folder == "Shoresy"


def test_scan_request_library_mode_allows_none_scan_path() -> None:
    req = ScanRequest(
        server_id=3,
        server_name="emby",
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="movies",
        scan_key="lib:movies",
        event_type=FsEventType.moved_to,
        file_path="/data/media/movies/Dune/Dune.mkv",
        top_folder=None,
    )
    assert req.scan_path is None
    assert req.top_folder is None
    assert req.scan_key == "lib:movies"


def test_scan_request_is_frozen_and_slotted() -> None:
    req = ScanRequest(
        server_id=1,
        server_name="x",
        scan_mode=ScanMode.targeted,
        scan_path="/a",
        library_id=None,
        scan_key="/a",
        event_type=FsEventType.deleted,
        file_path="/a/b.mkv",
        top_folder=None,
    )
    assert not hasattr(req, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.server_id = 99  # type: ignore[misc]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/pipeline/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.pipeline.events'`.

- [ ] **Step 4: Write minimal implementation**

Create `mediascanmonitor/pipeline/events.py` (verbatim from contract section 5):

```python
"""Domain event and scan-request types for the watcher → pipeline boundary.

Frozen, slotted dataclasses — these flow from the watcher (sub-plan 04) through the
router/debouncer/dispatcher (sub-plan 05) and must be cheap, immutable, and hashable
where needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mediascanmonitor.db.models import ScanMode


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
    scan_key: str              # debounce key: scan_path (targeted) or f"lib:{library_id}"
    # context (used by webhook templating in Phase 2; carried now):
    event_type: FsEventType
    file_path: str             # the originating absolute file path
    top_folder: str | None     # first path segment under the folder root (targeted), else None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/pipeline/test_events.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/pipeline/events.py tests/pipeline/__init__.py tests/pipeline/test_events.py
git commit -m "feat(pipeline): add FsEvent, FsEventType, ScanRequest domain types"
```

---

## Task 3: `config/runtime.py` — runtime dataclasses

**Files:**
- Create: `mediascanmonitor/config/runtime.py`
- Create: `tests/config/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Create `tests/config/test_runtime.py`:

```python
"""Tests for config/runtime.py — runtime snapshot dataclasses + builder."""

from __future__ import annotations

import dataclasses

import pytest
from mediascanmonitor.config.runtime import FolderRoute, RuntimeConfig, ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType


def test_server_runtime_fields_frozen_slotted() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="https://plex.local:32400",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="token-abc",
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    assert sr.server_id == 1
    assert sr.secret == "token-abc"
    assert sr.type is ServerType.plex
    assert not hasattr(sr, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        sr.secret = "leak"  # type: ignore[misc]


def test_server_runtime_secret_excluded_from_repr() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="super-secret-token",
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    assert "super-secret-token" not in repr(sr)  # invariant 3: never in a repr
    assert sr.secret == "super-secret-token"      # still reachable by attribute


def test_folder_route_fields_frozen_slotted() -> None:
    fr = FolderRoute(
        server_id=1,
        server_name="plex-main",
        path="/data/media/tv",
        extensions=frozenset({"mkv", "srt"}),
        library_id="2",
        scan_mode=ScanMode.targeted,
    )
    assert fr.path == "/data/media/tv"
    assert fr.extensions == frozenset({"mkv", "srt"})
    assert not hasattr(fr, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fr.path = "/elsewhere"  # type: ignore[misc]


def test_runtime_config_fields_frozen_slotted() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="",
        verify_tls=True,
        timeout_seconds=10.0,
        secret=None,
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    fr = FolderRoute(
        server_id=1,
        server_name="plex-main",
        path="/data/media/tv",
        extensions=frozenset(),
        library_id="2",
        scan_mode=ScanMode.targeted,
    )
    cfg = RuntimeConfig(
        watch_paths=frozenset({"/data/media/tv"}),
        routes=(fr,),
        servers={1: sr},
        ignore_dirs=frozenset({"@eaDir"}),
    )
    assert cfg.watch_paths == frozenset({"/data/media/tv"})
    assert cfg.routes == (fr,)
    assert cfg.servers == {1: sr}
    assert cfg.ignore_dirs == frozenset({"@eaDir"})
    assert not hasattr(cfg, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.routes = ()  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.config.runtime'`.

- [ ] **Step 3: Write minimal implementation**

Create `mediascanmonitor/config/runtime.py` (dataclasses verbatim from contract section 6;
`build_runtime_config` body is added in Task 4):

```python
"""Immutable runtime configuration snapshot, assembled from the DB.

The router and dispatcher (sub-plans 05/06) read this snapshot. Secrets are decrypted
into ``ServerRuntime.secret`` here (in memory only) — adapters receive plaintext tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType

if TYPE_CHECKING:
    from mediascanmonitor.db.repo import Repo


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
```

Note: the `if TYPE_CHECKING` import of `Repo` and the use of `DebounceMode`/`ScanMode`/
`ServerType` are wired in this step so the module is import-clean; `build_runtime_config`
(which uses `Repo`) lands in Task 4. To keep `ruff` happy in the interim (the `Repo` import and
the unused enum imports), Task 4 follows immediately — do not run `ruff` between Tasks 3 and 4.
`mypy` is fine because `TYPE_CHECKING` imports are allowed to be "unused" at runtime.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_runtime.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/config/runtime.py tests/config/test_runtime.py
git commit -m "feat(config): add ServerRuntime, FolderRoute, RuntimeConfig dataclasses"
```

---

## Task 4: `build_runtime_config` — happy path + `FakeRepo`

**Files:**
- Modify: `mediascanmonitor/config/runtime.py`
- Modify: `tests/config/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/config/test_runtime.py`. This adds the typed `FakeRepo` stub plus model
helpers, then the happy-path test:

```python
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from mediascanmonitor.config.runtime import build_runtime_config
from mediascanmonitor.db.models import FileType, Folder, Server

if TYPE_CHECKING:
    from mediascanmonitor.db.repo import Repo


@dataclass
class FakeRepo:
    """Typed structural stub for db.repo.Repo, exposing ONLY the methods that
    build_runtime_config calls. Returns transient (never session-added) section-2
    model instances; resolve_secret returns the already-"decrypted" plaintext."""

    servers: list[Server] = field(default_factory=list)
    folders_by_server: dict[int, list[Folder]] = field(default_factory=dict)
    secrets: dict[int, str | None] = field(default_factory=dict)

    def list_servers(self, *, enabled_only: bool = False) -> list[Server]:
        if enabled_only:
            return [s for s in self.servers if s.enabled]
        return list(self.servers)

    def list_folders(self, server_id: int) -> list[Folder]:
        return list(self.folders_by_server.get(server_id, []))

    def resolve_secret(self, server: Server) -> str | None:
        if server.id is None:
            return None
        return self.secrets.get(server.id)


def make_server(
    server_id: int,
    *,
    name: str,
    type: ServerType = ServerType.plex,
    base_url: str = "https://plex.local:32400",
    scan_mode: ScanMode = ScanMode.targeted,
    debounce_mode: DebounceMode = DebounceMode.trailing,
    enabled: bool = True,
) -> Server:
    return Server(
        id=server_id,
        name=name,
        type=type,
        base_url=base_url,
        verify_tls=True,
        timeout_seconds=10.0,
        secret_encrypted="ciphertext-ignored-by-stub",
        scan_mode=scan_mode,
        debounce_mode=debounce_mode,
        debounce_window_seconds=30,
        retry_attempts=3,
        enabled=enabled,
    )


def make_folder(
    folder_id: int,
    *,
    server_id: int,
    path: str,
    library_id: str | None,
    extensions: list[str],
    enabled: bool = True,
) -> Folder:
    folder = Folder(
        id=folder_id,
        server_id=server_id,
        path=path,
        library_id=library_id,
        enabled=enabled,
    )
    folder.filetypes = [
        FileType(id=None, folder_id=folder_id, extension=ext) for ext in extensions
    ]
    return folder


def test_build_runtime_config_happy_path() -> None:
    server = make_server(1, name="plex-main")
    folder = make_folder(
        10, server_id=1, path="/data/media/tv/", library_id="2", extensions=["MKV", ".srt"]
    )
    repo = FakeRepo(
        servers=[server],
        folders_by_server={1: [folder]},
        secrets={1: "plex-token-xyz"},
    )

    cfg = build_runtime_config(cast("Repo", repo))

    # One server, decrypted secret surfaced into ServerRuntime.
    assert set(cfg.servers) == {1}
    sr = cfg.servers[1]
    assert sr.server_id == 1
    assert sr.name == "plex-main"
    assert sr.type is ServerType.plex
    assert sr.secret == "plex-token-xyz"
    assert sr.scan_mode is ScanMode.targeted
    assert sr.debounce_mode is DebounceMode.trailing
    assert sr.debounce_window_seconds == 30
    assert sr.retry_attempts == 3

    # One route, normalized path (trailing slash stripped) + normalized extensions.
    assert len(cfg.routes) == 1
    route = cfg.routes[0]
    assert route.server_id == 1
    assert route.server_name == "plex-main"
    assert route.path == "/data/media/tv"
    assert route.extensions == frozenset({"mkv", "srt"})
    assert route.library_id == "2"
    assert route.scan_mode is ScanMode.targeted

    # Watch set is the normalized path; ignore dirs come from defaults.
    assert cfg.watch_paths == frozenset({"/data/media/tv"})
    assert "@eaDir" in cfg.ignore_dirs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_runtime.py::test_build_runtime_config_happy_path -v`
Expected: FAIL — `ImportError: cannot import name 'build_runtime_config'`.

- [ ] **Step 3: Write minimal implementation**

Add to `mediascanmonitor/config/runtime.py`. First extend the imports at the top to pull in
`IGNORE_DIRS` from `config/defaults.py` and the normalizers from the `normalize` leaf module
(place after the existing `from mediascanmonitor.db.models import ...` line):

```python
from mediascanmonitor.config.defaults import IGNORE_DIRS
from mediascanmonitor.normalize import normalize_extension, normalize_path
```

Then append the builder at the end of the module:

```python
def build_runtime_config(repo: Repo) -> RuntimeConfig:
    """Read enabled servers/folders/filetypes from the DB, decrypt secrets, and assemble the
    immutable snapshot. Disabled servers and their folders are excluded."""
    servers: dict[int, ServerRuntime] = {}
    routes: list[FolderRoute] = []
    watch_paths: set[str] = set()

    for server in repo.list_servers(enabled_only=True):
        assert server.id is not None  # persisted servers always carry an id
        server_id = server.id
        servers[server_id] = ServerRuntime(
            server_id=server_id,
            name=server.name,
            type=server.type,
            base_url=server.base_url,
            verify_tls=server.verify_tls,
            timeout_seconds=server.timeout_seconds,
            secret=repo.resolve_secret(server),
            scan_mode=server.scan_mode,
            debounce_mode=server.debounce_mode,
            debounce_window_seconds=server.debounce_window_seconds,
            retry_attempts=server.retry_attempts,
            webhook_method=server.webhook_method,
            webhook_headers_json=server.webhook_headers_json,
            webhook_body_template=server.webhook_body_template,
        )
        for folder in repo.list_folders(server_id):
            if not folder.enabled:
                continue
            path = normalize_path(folder.path)
            watch_paths.add(path)
            routes.append(
                FolderRoute(
                    server_id=server_id,
                    server_name=server.name,
                    path=path,
                    extensions=frozenset(
                        normalize_extension(ft.extension) for ft in folder.filetypes
                    ),
                    library_id=folder.library_id,
                    scan_mode=server.scan_mode,
                )
            )

    return RuntimeConfig(
        watch_paths=frozenset(watch_paths),
        routes=tuple(routes),
        servers=servers,
        ignore_dirs=IGNORE_DIRS,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_runtime.py::test_build_runtime_config_happy_path -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/config/runtime.py tests/config/test_runtime.py
git commit -m "feat(config): add build_runtime_config builder (happy path)"
```

---

## Task 5: `build_runtime_config` — exclusion, dedup, empty=all, multi-server

**Files:**
- Modify: `tests/config/test_runtime.py`

These behaviors are already implemented by Task 4's builder; this task pins them with tests.
(If any test fails, the builder — not the test — is wrong; fix `config/runtime.py`.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/test_runtime.py`:

```python
def test_disabled_server_excluded() -> None:
    enabled = make_server(1, name="plex-on", enabled=True)
    disabled = make_server(2, name="plex-off", enabled=False)
    repo = FakeRepo(
        servers=[enabled, disabled],
        folders_by_server={
            1: [make_folder(10, server_id=1, path="/data/tv", library_id="2", extensions=["mkv"])],
            2: [make_folder(20, server_id=2, path="/data/off", library_id="9", extensions=["mkv"])],
        },
        secrets={1: "tok1", 2: "tok2"},
    )

    cfg = build_runtime_config(cast("Repo", repo))

    assert set(cfg.servers) == {1}
    assert all(r.server_id == 1 for r in cfg.routes)
    assert cfg.watch_paths == frozenset({"/data/tv"})
    assert "/data/off" not in cfg.watch_paths


def test_disabled_folder_excluded() -> None:
    server = make_server(1, name="plex-main")
    on = make_folder(10, server_id=1, path="/data/tv", library_id="2", extensions=["mkv"])
    off = make_folder(
        11, server_id=1, path="/data/hidden", library_id="3", extensions=["mkv"], enabled=False
    )
    repo = FakeRepo(servers=[server], folders_by_server={1: [on, off]}, secrets={1: "tok"})

    cfg = build_runtime_config(cast("Repo", repo))

    assert cfg.watch_paths == frozenset({"/data/tv"})
    assert [r.path for r in cfg.routes] == ["/data/tv"]


def test_watch_paths_dedup_two_folders_same_path() -> None:
    # Two servers watch the SAME host path -> one watch path, two routes.
    s1 = make_server(1, name="plex")
    s2 = make_server(2, name="emby", type=ServerType.emby, scan_mode=ScanMode.library)
    repo = FakeRepo(
        servers=[s1, s2],
        folders_by_server={
            1: [make_folder(10, server_id=1, path="/data/tv/", library_id="2", extensions=["mkv"])],
            2: [make_folder(20, server_id=2, path="/data/tv", library_id="5", extensions=["mkv"])],
        },
        secrets={1: "a", 2: "b"},
    )

    cfg = build_runtime_config(cast("Repo", repo))

    assert cfg.watch_paths == frozenset({"/data/tv"})
    assert len(cfg.routes) == 2
    assert {r.server_id for r in cfg.routes} == {1, 2}


def test_empty_filetypes_means_all_extensions() -> None:
    server = make_server(1, name="plex-main")
    folder = make_folder(10, server_id=1, path="/data/tv", library_id="2", extensions=[])
    repo = FakeRepo(servers=[server], folders_by_server={1: [folder]}, secrets={1: "tok"})

    cfg = build_runtime_config(cast("Repo", repo))

    assert cfg.routes[0].extensions == frozenset()


def test_secret_none_when_unresolved() -> None:
    server = make_server(1, name="plex-main")
    folder = make_folder(10, server_id=1, path="/data/tv", library_id="2", extensions=["mkv"])
    repo = FakeRepo(servers=[server], folders_by_server={1: [folder]}, secrets={})

    cfg = build_runtime_config(cast("Repo", repo))

    assert cfg.servers[1].secret is None


def test_empty_repo_yields_empty_config() -> None:
    cfg = build_runtime_config(cast("Repo", FakeRepo()))
    assert cfg.servers == {}
    assert cfg.routes == ()
    assert cfg.watch_paths == frozenset()
    assert "@eaDir" in cfg.ignore_dirs
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/config/test_runtime.py -v`
Expected: PASS (all runtime tests — the Task 4 happy path plus the 6 new tests).

If `test_empty_filetypes_means_all_extensions` or any dedup/exclusion test FAILS, the builder is
wrong — re-read Task 4's implementation and fix `config/runtime.py` (do not weaken the test).

- [ ] **Step 3: Commit**

```bash
git add tests/config/test_runtime.py
git commit -m "test(config): pin exclusion, dedup, empty=all, multi-server runtime build"
```

---

## Task 6: Full quality gate (ruff + mypy --strict + pytest)

**Files:** none changed unless a check fails.

- [ ] **Step 1: Run ruff lint**

Run: `ruff check mediascanmonitor/config mediascanmonitor/pipeline tests/config tests/pipeline`
Expected: `All checks passed!`

If ruff reports issues, fix them (common ones: import ordering — ruff's `I` rule will autofix
with `ruff check --fix`; line length > 100). Re-run until clean.

- [ ] **Step 2: Run ruff format check**

Run: `ruff format --check mediascanmonitor/config mediascanmonitor/pipeline tests/config tests/pipeline`
Expected: `N files already formatted`.

If it reports files would be reformatted, run `ruff format <those files>` and re-commit.

- [ ] **Step 3: Run mypy --strict**

Run: `mypy mediascanmonitor/config mediascanmonitor/pipeline`
Expected: `Success: no issues found in 3 source files`.

If mypy complains about `server.id` being `int | None`, confirm the `assert server.id is not None`
line is present in `build_runtime_config` (it narrows the type). If it complains about the
`TYPE_CHECKING` import of `Repo`, confirm `from __future__ import annotations` is the first import
in `runtime.py`.

- [ ] **Step 4: Run the full sub-plan test suite**

Run: `pytest tests/config tests/pipeline -v`
Expected: PASS — all tests from Tasks 1–5 (defaults, events, runtime).

- [ ] **Step 5: Run the entire repository test suite (no regressions)**

Run: `pytest -q`
Expected: PASS — including the Phase 0 `tests/test_cli.py` smoke tests.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "chore(config,pipeline): ruff + mypy --strict clean for sub-plan 02"
```

(If Steps 1–5 required no changes, skip this commit.)

---

## Self-Review

**1. Spec coverage** (contract sections 5, 6, and cross-plan invariants in scope):

| Spec item | Task |
|---|---|
| `IGNORE_DIRS` (`@eaDir`, `#snapshot`, `#recycle`, `@tmp`) | Task 1 |
| `EXTENSION_PRESETS` (video/subtitles/audio) | Task 1 |
| `DEFAULT_DEBOUNCE_WINDOW_SECONDS = 30` | Task 1 |
| `DEFAULT_DEBOUNCE_BY_TYPE` (media=trailing, webhook=off) | Task 1 |
| `FsEventType` enum (4 values) | Task 2 |
| `FsEvent` frozen slotted dataclass | Task 2 |
| `ScanRequest` frozen slotted dataclass | Task 2 |
| `ServerRuntime` frozen slotted dataclass (`secret` excluded from repr) | Task 3 |
| `FolderRoute` frozen slotted dataclass | Task 3 |
| `RuntimeConfig` frozen slotted dataclass | Task 3 |
| `build_runtime_config` reads enabled servers/folders/filetypes | Tasks 4, 5 |
| disabled servers/folders excluded | Task 5 |
| `watch_paths` deduplicated union | Task 5 |
| one `FolderRoute` per enabled (server, folder) | Tasks 4, 5 |
| extensions frozenset of normalized; empty → empty (all) | Tasks 4, 5 |
| `servers` keyed by id; secret decrypted via `resolve_secret` | Tasks 4, 5 |
| `ignore_dirs` from `IGNORE_DIRS` | Task 4 |
| paths normalized via `normalize_path` (invariant 4) | Tasks 4, 5 |
| secrets only in `ServerRuntime.secret`, not in repr (invariant 3) | Tasks 3, 4, 5 |
| mypy --strict + ruff clean | Task 6 |

`normalize_extension` / `normalize_path` are **not** in this table — they are owned by sub-plan
01 (`mediascanmonitor/normalize.py`, contract §1.1) and tested there; this sub-plan only imports
them. No gaps. Items explicitly **out of scope** (other sub-plans): extension *matching* semantics
(`extension_matches`, sub-plan 05), the `scan_key` derivation in `route()` (invariant 2,
sub-plan 05), prefix matching (invariant 5, sub-plan 05), failure isolation (invariant 6,
sub-plan 05/06). The `ScanRequest.scan_key` *field* is defined here (Task 2); it is *populated*
by the router in sub-plan 05.

**2. Placeholder scan:** No "TBD/TODO/implement later". Every code step shows complete code; every
test step shows complete test code; every run step shows the exact command and expected output.

**3. Type consistency:**
- `normalize_extension` / `normalize_path` are imported from `mediascanmonitor.normalize`
  (sub-plan 01) in both `config/defaults.py`'s consumers and `runtime.py` (Task 4) — one
  definition, never redefined here. ✓
- `ServerRuntime` / `FolderRoute` / `RuntimeConfig` field names match the contract verbatim
  (incl. `secret: str | None = field(repr=False)`) and are used consistently in Tasks 3–5. ✓
- `FakeRepo` methods (`list_servers`, `list_folders`, `resolve_secret`) match the section-4 `Repo`
  signatures the builder calls. ✓
- `make_server` / `make_folder` helper signatures match the model fields from contract section 2
  (`Server`: `enabled`, `scan_mode`, `debounce_mode`, etc.; `Folder`: `path`, `library_id`,
  `enabled`, `filetypes`; `FileType`: `folder_id`, `extension`). ✓
- `build_runtime_config(repo: Repo)` signature is verbatim from the contract; tests pass
  `cast("Repo", FakeRepo(...))`. ✓

One note carried into execution: Tasks 3→4 leave `runtime.py` momentarily importing `Repo` (under
`TYPE_CHECKING`) and the enums before the builder uses them. This is mypy-clean
(`TYPE_CHECKING` imports may be runtime-unused) and the Task 3 note instructs running `ruff` only
after Task 4. No fix needed beyond that sequencing note.
