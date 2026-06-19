# Phase 1 — Sub-plan 04: Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mediascanmonitor/watcher/` package — a `WatcherBackend` protocol, a portable `FakeWatcher` test double, the kernel watch-limit gate, and a recursive `asyncinotify` backend that emits `FsEvent`s — exactly per the FROZEN interface contract section 8.

**Architecture:** Raw inotify is not recursive, so `InotifyBackend` keeps one watch per directory under each root and dynamically adds a watch (plus rescans existing contents into synthetic `created` events to close the mkdir→add_watch attach race) when a subdirectory appears, and removes watches when one is deleted/moved away. Mask→`FsEventType` mapping is a pure, asyncinotify-free helper so it is unit-testable on any platform; the asyncinotify C bindings (Linux-only) are imported lazily inside `InotifyBackend.__init__`, which keeps the module — and the unit tests that import it — portable. The backend does **no** extension filtering (that is the pipeline's job); it only skips `ignore_dirs` path segments and normalizes paths.

**Tech Stack:** Python ≥ 3.14, `asyncinotify==4.4.4`, `pytest==9.1.0` + `pytest-asyncio==1.4.0` (`asyncio_mode = "auto"`), `mypy --strict`, `ruff` (line length 100).

---

## Contract dependencies (consume verbatim — do NOT redefine)

These already exist (or are delivered by earlier sub-plans 02) when this plan runs. **Import** them; never re-declare them:

- `FsEvent`, `FsEventType` from `mediascanmonitor.pipeline.events` (contract §5, owned by sub-plan 02).
- `normalize_path` from `mediascanmonitor.normalize` (contract §1.1, leaf module owned by sub-plan 01) — pure lexical normalize, no trailing slash (except root); absoluteness is validated upstream at the schema boundary, not here.
- `IGNORE_DIRS` (`frozenset[str]`, e.g. `{"@eaDir", "#snapshot", "#recycle", "@tmp"}`) from `mediascanmonitor.config.defaults` is the typical caller-supplied `ignore_dirs`; the watcher accepts whatever `frozenset[str]` it is constructed with.

**Contract §5 (for reference):**

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
```

**Contract §8 — the surface this plan must produce verbatim:**

```python
# watcher/base.py
class WatcherBackend(Protocol):
    def set_roots(self, roots: set[str]) -> None: ...
    def events(self) -> AsyncIterator[FsEvent]: ...
    async def aclose(self) -> None: ...

# watcher/inotify_backend.py
class InotifyBackend:                                # implements WatcherBackend
    def __init__(self, ignore_dirs: frozenset[str]) -> None: ...

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

---

## File Structure

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/watcher/base.py` | `WatcherBackend` Protocol **and** the `FakeWatcher` test double (queue-fed, no inotify) so sub-plans 05/06 and non-Linux dev can drive events. |
| `mediascanmonitor/watcher/watch_limit.py` | `WatchLimitStatus`, `read_max_user_watches`, `count_dirs`, `check_watch_limit`. Pure stdlib; runs everywhere. |
| `mediascanmonitor/watcher/inotify_backend.py` | Pure mask helpers (`mask_to_event_type`, `mask_is_dir`, `IN_*` bit constants) + `InotifyBackend` recursive watcher. asyncinotify imported lazily inside `__init__`. |
| `tests/watcher/__init__.py` | Test package marker. |
| `tests/watcher/test_fake_watcher.py` | UNIT (everywhere): `FakeWatcher` behavior. |
| `tests/watcher/test_watch_limit.py` | UNIT (everywhere): the three watch-limit functions. |
| `tests/watcher/test_mask_mapping.py` | UNIT (everywhere): pure `mask_to_event_type` / `mask_is_dir`. |
| `tests/watcher/test_inotify_backend.py` | INTEGRATION (Linux-only): real `InotifyBackend` on `tmp_path`. |

**Why `FakeWatcher` lives in `base.py` (production code, not a tests/ helper):** sub-plan 06's `Engine` accepts an injected `WatcherBackend`, and the contract states (§10) "The watcher is injectable into `Engine` so non-Linux dev/tests can pass a fake backend." Shipping `FakeWatcher` next to the protocol it implements lets the engine, the pipeline tests, and a developer on macOS import it from the package without reaching into `tests/`.

**Portability rule honored throughout:** `asyncinotify` ships Linux-only C/`ctypes` bindings. To keep `import mediascanmonitor.watcher.inotify_backend` working on any OS (so the *pure* mask-mapping unit tests run everywhere), the asyncinotify import is deferred to `InotifyBackend.__init__`; the module top imports asyncinotify names only under `TYPE_CHECKING`. Only *instantiating* `InotifyBackend` requires Linux.

---

## Test markers & determinism

- `pyproject.toml` already sets `asyncio_mode = "auto"`, so `async def test_*` functions run without an explicit marker. No new pytest config is needed.
- **Linux-only integration tests** use a module-level `pytestmark`:
  ```python
  pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="inotify backend is Linux-only")
  ```
  Because `inotify_backend` imports asyncinotify lazily (not at module top), collecting/importing the test module is safe on every platform; the skip only prevents the bodies (which construct `InotifyBackend`) from running off-Linux.
- **No arbitrary `sleep`s in integration tests.** inotify queues events in the kernel, so the pattern is: perform the filesystem operation, *then* pull the next event from the async iterator with a bounded `asyncio.wait_for(...)`. A shared `next_event(agen, timeout)` helper raises `TimeoutError` if nothing arrives — which is exactly how the "ignore dir produces no event" case is asserted (negative assertion via timeout) and how every positive case stays deterministic instead of racing a fixed sleep.

---

## Task 1: `WatcherBackend` protocol + `FakeWatcher`

**Files:**
- Create: `mediascanmonitor/watcher/base.py`
- Create: `tests/watcher/__init__.py`
- Test: `tests/watcher/test_fake_watcher.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/watcher/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the failing test**

Create `tests/watcher/test_fake_watcher.py`:

```python
"""Unit tests for the FakeWatcher test double (runs on every platform)."""

from __future__ import annotations

import asyncio

from mediascanmonitor.pipeline.events import FsEvent, FsEventType
from mediascanmonitor.watcher.base import FakeWatcher, WatcherBackend


def test_fakewatcher_is_a_watcherbackend() -> None:
    fake = FakeWatcher()
    assert isinstance(fake, WatcherBackend)


def test_set_roots_is_recorded() -> None:
    fake = FakeWatcher()
    fake.set_roots({"/data/a", "/data/b"})
    assert fake.roots == {"/data/a", "/data/b"}


async def test_events_yields_injected_list_then_stops() -> None:
    seeded = [
        FsEvent("/data/a/movie.mkv", FsEventType.created, is_dir=False),
        FsEvent("/data/a/other.mkv", FsEventType.deleted, is_dir=False),
    ]
    fake = FakeWatcher(seeded)
    fake.close_stream()  # sentinel so `events()` terminates after draining

    collected = [event async for event in fake.events()]

    assert collected == seeded


async def test_feed_delivers_events_to_a_live_consumer() -> None:
    fake = FakeWatcher()
    agen = fake.events()

    fake.feed(FsEvent("/data/a/late.mkv", FsEventType.moved_to, is_dir=False))
    event = await asyncio.wait_for(agen.__anext__(), timeout=1.0)

    assert event.path == "/data/a/late.mkv"
    assert event.event_type is FsEventType.moved_to
    await fake.aclose()


async def test_aclose_terminates_the_stream() -> None:
    fake = FakeWatcher()
    agen = fake.events()
    await fake.aclose()

    collected = [event async for event in agen]

    assert collected == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/watcher/test_fake_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.watcher.base'` (or `ImportError: cannot import name 'FakeWatcher'`).

- [ ] **Step 4: Write the implementation**

Create `mediascanmonitor/watcher/base.py`:

```python
"""Watcher backend protocol and a portable in-memory fake.

`WatcherBackend` is the only surface the engine depends on. `FakeWatcher` is a
queue-fed implementation that needs no inotify, so the pipeline/engine tests and
non-Linux development can drive `FsEvent`s deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Protocol, runtime_checkable

from mediascanmonitor.pipeline.events import FsEvent


@runtime_checkable
class WatcherBackend(Protocol):
    """A recursive filesystem watcher that yields `FsEvent`s."""

    def set_roots(self, roots: set[str]) -> None:
        """Set the recursive watch roots. Idempotent diff against the current set."""
        ...

    def events(self) -> AsyncIterator[FsEvent]:
        """Return an async iterator over filesystem events."""
        ...

    async def aclose(self) -> None:
        """Release all resources and terminate the event stream."""
        ...


class FakeWatcher:
    """In-memory `WatcherBackend` driven by an injected list and/or `feed()`/`emit()`.

    This is the **single canonical** test watcher for all of Phase 1: sub-plans 05 and 06
    import it from here (see `2026-06-17-phase1-README.md`) rather than redefining their own.

    `events()` drains an internal queue; a `None` sentinel (enqueued by `close_stream()` or
    `aclose()`) ends the stream so `async for` completes. Test affordances used by the engine
    rebuild tests (sub-plan 06): `roots_history` records every `set_roots` call (assert
    watch-set diffs across `rebuild()`), `current_roots` is the latest set, and `closed`
    flips on `aclose()`.
    """

    def __init__(self, events: Iterable[FsEvent] = ()) -> None:
        self.roots: set[str] = set()
        self.roots_history: list[set[str]] = []
        self.closed = False
        self._queue: asyncio.Queue[FsEvent | None] = asyncio.Queue()
        for event in events:
            self._queue.put_nowait(event)

    def set_roots(self, roots: set[str]) -> None:
        self.roots = set(roots)
        self.roots_history.append(set(roots))

    @property
    def current_roots(self) -> set[str]:
        return self.roots_history[-1] if self.roots_history else self.roots

    def feed(self, event: FsEvent) -> None:
        """Push one event to live consumers of `events()` (sync)."""
        self._queue.put_nowait(event)

    async def emit(self, event: FsEvent) -> None:
        """Async alias for `feed()` — await-friendly inside async tests."""
        self._queue.put_nowait(event)

    def close_stream(self) -> None:
        """Signal end-of-stream without tearing down the watcher."""
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[FsEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def aclose(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/watcher/test_fake_watcher.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Lint & type-check the new files**

Run: `ruff check mediascanmonitor/watcher/base.py tests/watcher/ && ruff format --check mediascanmonitor/watcher/base.py tests/watcher/ && mypy mediascanmonitor/watcher/base.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/watcher/base.py tests/watcher/__init__.py tests/watcher/test_fake_watcher.py
git commit -m "feat(watcher): add WatcherBackend protocol and FakeWatcher test double"
```

---

## Task 2: `read_max_user_watches`

**Files:**
- Create: `mediascanmonitor/watcher/watch_limit.py`
- Test: `tests/watcher/test_watch_limit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/watcher/test_watch_limit.py`:

```python
"""Unit tests for the inotify watch-limit gate (runs on every platform)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mediascanmonitor.watcher import watch_limit


def test_read_max_user_watches_parses_the_proc_file(tmp_path: Path) -> None:
    proc = tmp_path / "max_user_watches"
    proc.write_text("131072\n")

    assert watch_limit.read_max_user_watches(str(proc)) == 131072


def test_read_max_user_watches_strips_surrounding_whitespace(tmp_path: Path) -> None:
    proc = tmp_path / "max_user_watches"
    proc.write_text("  8192  \n")

    assert watch_limit.read_max_user_watches(str(proc)) == 8192
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/watcher/test_watch_limit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.watcher.watch_limit'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/watcher/watch_limit.py`:

```python
"""inotify `max_user_watches` gate.

Per-directory watches consume the kernel `fs.inotify.max_user_watches` budget.
These helpers count the directories a config will watch and compare against the
current limit (with headroom) so the engine/dashboard can surface a clear
"raise your watch limit" signal — re-implementing the legacy script's gate.
"""

from __future__ import annotations

from pathlib import Path


def read_max_user_watches(proc_path: str = "/proc/sys/fs/inotify/max_user_watches") -> int:
    """Return the current `max_user_watches` kernel limit."""
    return int(Path(proc_path).read_text().strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/watcher/test_watch_limit.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/watcher/watch_limit.py tests/watcher/test_watch_limit.py
git commit -m "feat(watcher): add read_max_user_watches"
```

---

## Task 3: `count_dirs`

**Files:**
- Modify: `mediascanmonitor/watcher/watch_limit.py`
- Test: `tests/watcher/test_watch_limit.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/watcher/test_watch_limit.py`:

```python
def test_count_dirs_counts_root_and_subdirs_skipping_ignored(tmp_path: Path) -> None:
    # tmp_path
    #   show_a/
    #     season_1/
    #   show_b/
    #   @eaDir/            <- ignored (and its children must not be counted)
    #     thumbs/
    (tmp_path / "show_a" / "season_1").mkdir(parents=True)
    (tmp_path / "show_b").mkdir()
    (tmp_path / "@eaDir" / "thumbs").mkdir(parents=True)

    # Counted dirs: tmp_path, show_a, show_a/season_1, show_b  -> 4
    count = watch_limit.count_dirs([str(tmp_path)], frozenset({"@eaDir"}))

    assert count == 4


def test_count_dirs_skips_missing_roots(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"

    assert watch_limit.count_dirs([str(missing)], frozenset()) == 0


def test_count_dirs_sums_multiple_roots(tmp_path: Path) -> None:
    (tmp_path / "r1" / "sub").mkdir(parents=True)
    (tmp_path / "r2").mkdir()

    # r1 + r1/sub = 2 ; r2 = 1 ; total 3
    count = watch_limit.count_dirs(
        [str(tmp_path / "r1"), str(tmp_path / "r2")], frozenset()
    )

    assert count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/watcher/test_watch_limit.py -k count_dirs -v`
Expected: FAIL — `AttributeError: module 'mediascanmonitor.watcher.watch_limit' has no attribute 'count_dirs'`.

- [ ] **Step 3: Write the implementation**

Edit `mediascanmonitor/watcher/watch_limit.py` — update the import line and append `count_dirs`:

```python
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
```

```python
def count_dirs(roots: Iterable[str], ignore_dirs: frozenset[str]) -> int:
    """Count directories that will be watched: each root plus every descendant
    directory, skipping any directory named in `ignore_dirs` (and its subtree).
    Missing roots contribute zero.
    """
    total = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for _dirpath, dirnames, _filenames in os.walk(root):
            # Prune ignored directories in place so os.walk never descends them.
            dirnames[:] = [name for name in dirnames if name not in ignore_dirs]
            total += 1  # count the current directory (root counted once at the top)
    return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/watcher/test_watch_limit.py -k count_dirs -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/watcher/watch_limit.py tests/watcher/test_watch_limit.py
git commit -m "feat(watcher): add count_dirs with ignore-dir pruning"
```

---

## Task 4: `WatchLimitStatus` + `check_watch_limit`

**Files:**
- Modify: `mediascanmonitor/watcher/watch_limit.py`
- Test: `tests/watcher/test_watch_limit.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/watcher/test_watch_limit.py`:

```python
def test_check_watch_limit_ok_with_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one dir to watch (the root); default headroom 1.2 -> needed = ceil(1*1.2) = 2
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 100)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset())

    assert status.current == 100
    assert status.dirs == 1               # raw dir count
    assert status.needed == 2             # ceil(1 * 1.2)
    assert status.recommended == 3        # ceil(2 * 1.2)
    assert status.ok is True


def test_check_watch_limit_not_ok_when_below_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build 100 directories under the root: raw dir count = 101 (root + 100).
    for i in range(100):
        (tmp_path / f"d{i}").mkdir()
    # headroom 1.2 -> needed = ceil(101*1.2) = ceil(121.2) = 122 ; 121 is below it.
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 121)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset())

    assert status.dirs == 101
    assert status.needed == 122
    assert status.ok is False  # 121 < 122


def test_check_watch_limit_ok_exactly_at_headroom_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # raw dir count = 1 (root only) ; headroom 2.0 -> needed = ceil(2.0) = 2 ; limit 2 is OK.
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 2)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset(), headroom=2.0)

    assert status.dirs == 1
    assert status.needed == 2
    assert status.ok is True  # 2 >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/watcher/test_watch_limit.py -k check_watch_limit -v`
Expected: FAIL — `AttributeError: ... has no attribute 'check_watch_limit'`.

- [ ] **Step 3: Write the implementation**

Edit `mediascanmonitor/watcher/watch_limit.py` — extend the import block and append the dataclass + function:

```python
from __future__ import annotations

import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
```

```python
@dataclass(frozen=True, slots=True)
class WatchLimitStatus:
    current: int       # live kernel ceiling read from /proc
    dirs: int          # raw count of directories that will be watched (one watch each)
    needed: int        # gate threshold = ceil(dirs * headroom)
    recommended: int   # kernel ceiling to advise the user = ceil(needed * headroom)
    ok: bool           # current >= needed


def check_watch_limit(
    roots: Iterable[str],
    ignore_dirs: frozenset[str],
    headroom: float = 1.2,
) -> WatchLimitStatus:
    """Measure the directories a config will watch and compare against the current
    kernel watch limit. The app *measures* `needed`; it never stores a target
    (contract §8). `needed = ceil(dirs * headroom)`, the gate is simply
    `current >= needed`, and `recommended` is the kernel ceiling to advise the user.
    """
    dirs = count_dirs(roots, ignore_dirs)
    needed = math.ceil(dirs * headroom)
    recommended = math.ceil(needed * headroom)
    current = read_max_user_watches()
    return WatchLimitStatus(
        current=current,
        dirs=dirs,
        needed=needed,
        recommended=recommended,
        ok=current >= needed,
    )
```

- [ ] **Step 4: Run the full watch-limit suite to verify it passes**

Run: `python -m pytest tests/watcher/test_watch_limit.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint & type-check**

Run: `ruff check mediascanmonitor/watcher/watch_limit.py tests/watcher/test_watch_limit.py && mypy mediascanmonitor/watcher/watch_limit.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/watcher/watch_limit.py tests/watcher/test_watch_limit.py
git commit -m "feat(watcher): add WatchLimitStatus and check_watch_limit"
```

---

## Task 5: Pure mask→`FsEventType` helpers

**Files:**
- Create: `mediascanmonitor/watcher/inotify_backend.py`
- Test: `tests/watcher/test_mask_mapping.py`

This task creates the module with **only** the pure helpers and the inotify bit constants. The `InotifyBackend` class is added in Task 6. Keeping the helpers asyncinotify-free is what makes them testable on every platform.

- [ ] **Step 1: Write the failing test**

Create `tests/watcher/test_mask_mapping.py`:

```python
"""Unit tests for the pure inotify-mask helpers (runs on every platform).

These import `inotify_backend` but never construct `InotifyBackend`, so they do
not touch the Linux-only asyncinotify C bindings.
"""

from __future__ import annotations

from mediascanmonitor.pipeline.events import FsEventType
from mediascanmonitor.watcher import inotify_backend as ib


def test_create_maps_to_created() -> None:
    assert ib.mask_to_event_type(ib.IN_CREATE) is FsEventType.created


def test_moved_to_maps_to_moved_to() -> None:
    assert ib.mask_to_event_type(ib.IN_MOVED_TO) is FsEventType.moved_to


def test_delete_maps_to_deleted() -> None:
    assert ib.mask_to_event_type(ib.IN_DELETE) is FsEventType.deleted


def test_moved_from_maps_to_moved_from() -> None:
    assert ib.mask_to_event_type(ib.IN_MOVED_FROM) is FsEventType.moved_from


def test_irrelevant_mask_maps_to_none() -> None:
    # IN_IGNORED (0x8000) and IN_ISDIR alone carry no create/move/delete bit.
    assert ib.mask_to_event_type(0x8000) is None
    assert ib.mask_to_event_type(ib.IN_ISDIR) is None
    # IN_Q_OVERFLOW is not a file event — events() handles it separately (resync).
    assert ib.mask_to_event_type(ib.IN_Q_OVERFLOW) is None


def test_create_with_isdir_still_maps_to_created() -> None:
    assert ib.mask_to_event_type(ib.IN_CREATE | ib.IN_ISDIR) is FsEventType.created


def test_mask_is_dir_detects_isdir_bit() -> None:
    assert ib.mask_is_dir(ib.IN_CREATE | ib.IN_ISDIR) is True
    assert ib.mask_is_dir(ib.IN_CREATE) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/watcher/test_mask_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.watcher.inotify_backend'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/watcher/inotify_backend.py` (helpers + constants only for now):

```python
"""Asyncinotify-backed recursive watcher.

Raw inotify is not recursive, so this backend adds one watch per directory under
each configured root and dynamically adds/removes watches as subdirectories are
created, moved, or deleted. When a new subdirectory appears, its existing
contents are rescanned and emitted as synthetic `created` events to close the
window between `mkdir` and `add_watch` (the "attach race").

This module performs NO extension filtering — that is the pipeline's job. It only
skips `ignore_dirs` path segments (e.g. Synology `@eaDir`/`#snapshot`) and
normalizes paths via `normalize_path`.

asyncinotify ships Linux-only C bindings, so it is imported lazily inside
`InotifyBackend.__init__`. The module top (and the pure mask helpers below)
import on any platform, which keeps the mask-mapping unit tests portable. We do
NOT use asyncinotify's `add_watch(recursive=True)`: per-directory control is
required for the watch-limit gate and for the attach-race rescan.
"""

from __future__ import annotations

from mediascanmonitor.pipeline.events import FsEventType

# inotify event bit constants (stable Linux kernel ABI). Defined locally so the
# pure mapping helpers need no asyncinotify import and run on any platform.
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_Q_OVERFLOW = 0x00004000   # kernel queue overflow — events were dropped; triggers a resync
IN_ISDIR = 0x40000000


def mask_to_event_type(mask: int) -> FsEventType | None:
    """Map a raw inotify event mask to an `FsEventType`, or `None` if the mask
    carries no create/move/delete signal we care about (e.g. `IN_IGNORED`).
    """
    if mask & IN_CREATE:
        return FsEventType.created
    if mask & IN_MOVED_TO:
        return FsEventType.moved_to
    if mask & IN_DELETE:
        return FsEventType.deleted
    if mask & IN_MOVED_FROM:
        return FsEventType.moved_from
    return None


def mask_is_dir(mask: int) -> bool:
    """True if the event concerns a directory (the `IN_ISDIR` bit is set)."""
    return bool(mask & IN_ISDIR)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/watcher/test_mask_mapping.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Lint & type-check**

Run: `ruff check mediascanmonitor/watcher/inotify_backend.py tests/watcher/test_mask_mapping.py && mypy mediascanmonitor/watcher/inotify_backend.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/watcher/inotify_backend.py tests/watcher/test_mask_mapping.py
git commit -m "feat(watcher): add pure inotify mask->FsEventType helpers"
```

---

## Task 6: `InotifyBackend` — flat watching (create / delete / move / ignore)

**Files:**
- Modify: `mediascanmonitor/watcher/inotify_backend.py`
- Modify: `pyproject.toml` (mypy override for asyncinotify)
- Test: `tests/watcher/test_inotify_backend.py`

This task implements `set_roots` (recursive watch of the *existing* tree), `events()` mapping for file-level events with ignore-dir skipping and path normalization, and `aclose()`. Runtime dynamic watch-add on **new** subdirectories is added in Task 7.

- [ ] **Step 1: Add the mypy per-module override for asyncinotify**

asyncinotify is imported here for the first time in the project. Edit `pyproject.toml`, appending after the existing `[tool.mypy]` block (just before `[tool.pytest.ini_options]`):

```toml
[[tool.mypy.overrides]]
module = ["asyncinotify.*"]
ignore_missing_imports = true
```

(If asyncinotify ships a `py.typed` marker and mypy resolves it cleanly, `warn_unused_configs`/`warn_unused_ignores` will flag nothing here because this is an `ignore_missing_imports` override, not an inline `# type: ignore`. Keep the override — it is the documented place for third-party C-extension stubs and protects the build if the package is absent on a contributor's machine.)

- [ ] **Step 2: Write the failing integration tests**

Create `tests/watcher/test_inotify_backend.py`:

```python
"""Integration tests for the real asyncinotify backend (Linux-only).

Determinism: we never sleep for a fixed period. inotify queues events in the
kernel, so each test performs a filesystem operation and then pulls the next
event(s) from the async iterator with a bounded `asyncio.wait_for`. The
ignore-dir test asserts the *absence* of an event via `TimeoutError`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mediascanmonitor.pipeline.events import FsEvent, FsEventType
from mediascanmonitor.watcher.inotify_backend import InotifyBackend

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="inotify backend is Linux-only"
)

IGNORE = frozenset({"@eaDir", "#snapshot"})
TIMEOUT = 2.0


async def next_event(agen: AsyncIterator[FsEvent], timeout: float = TIMEOUT) -> FsEvent:
    """Pull the next event or raise TimeoutError — no arbitrary sleeps."""
    import asyncio

    return await asyncio.wait_for(agen.__anext__(), timeout)


async def collect_event_for(
    agen: AsyncIterator[FsEvent], target_path: str, timeout: float = TIMEOUT
) -> FsEvent:
    """Drain events until one matches target_path (tolerates synthetic/dir events)."""
    import asyncio

    async def _scan() -> FsEvent:
        async for event in agen:
            if event.path == target_path:
                return event
        raise AssertionError("stream ended before target event")

    return await asyncio.wait_for(_scan(), timeout)


async def test_file_creation_emits_created_event(tmp_path: Path) -> None:
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        target = tmp_path / "movie.mkv"
        target.write_text("x")

        event = await collect_event_for(agen, str(target))

        assert event.event_type is FsEventType.created
        assert event.is_dir is False
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_file_deletion_emits_deleted_event(tmp_path: Path) -> None:
    target = tmp_path / "movie.mkv"
    target.write_text("x")  # exists before watching
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        target.unlink()

        event = await collect_event_for(agen, str(target))

        assert event.event_type is FsEventType.deleted
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_events_in_preexisting_subdir_are_watched(tmp_path: Path) -> None:
    # set_roots must recurse into directories that already exist.
    show = tmp_path / "Shoresy" / "Season 01"
    show.mkdir(parents=True)
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        target = show / "s01e01.mkv"
        target.write_text("x")

        event = await collect_event_for(agen, str(target))

        assert event.event_type is FsEventType.created
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_ignore_dir_contents_produce_no_events(tmp_path: Path) -> None:
    eadir = tmp_path / "@eaDir"
    eadir.mkdir()  # ignored at creation; no watch attached
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        # Activity inside an ignored dir must be invisible...
        (eadir / "poster.jpg").write_text("x")
        # ...but a real file at the root must still surface. Because events are
        # ordered, the first event we see proves the @eaDir activity was dropped.
        real = tmp_path / "movie.mkv"
        real.write_text("x")

        event = await next_event(agen)

        assert event.path == str(real)
        assert event.event_type is FsEventType.created
    finally:
        await agen.aclose()
        await backend.aclose()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/watcher/test_inotify_backend.py -v`
Expected (on Linux): FAIL — `ImportError: cannot import name 'InotifyBackend'`. (On non-Linux: all skipped — acceptable, but implement/verify on Linux.)

- [ ] **Step 4: Write the implementation**

Edit `mediascanmonitor/watcher/inotify_backend.py`. Replace the import block at the top of the file:

```python
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mediascanmonitor.normalize import normalize_path
from mediascanmonitor.pipeline.events import FsEvent, FsEventType

if TYPE_CHECKING:
    from asyncinotify import Inotify, Watch

logger = logging.getLogger(__name__)
```

Then append the `InotifyBackend` class to the end of the file:

```python
class InotifyBackend:
    """Recursive `WatcherBackend` built on asyncinotify (one watch per directory)."""

    def __init__(self, ignore_dirs: frozenset[str]) -> None:
        from asyncinotify import Inotify, Mask

        self._ignore_dirs = ignore_dirs
        self._inotify: Inotify = Inotify()
        self._add_mask = Mask.CREATE | Mask.MOVED_TO | Mask.DELETE | Mask.MOVED_FROM
        self._watches: dict[str, Watch] = {}
        self._roots: set[str] = set()

    # -- internal watch bookkeeping -----------------------------------------
    def _is_ignored(self, path: str) -> bool:
        return any(segment in self._ignore_dirs for segment in path.split(os.sep))

    def _add_watch(self, path: str) -> None:
        if path in self._watches:
            return
        try:
            self._watches[path] = self._inotify.add_watch(path, self._add_mask)
        except OSError as exc:
            # Adding a watch can fail at runtime (kernel limit / ENOSPC) even though the
            # startup gate passed, because watches grow as directories appear. Degrade:
            # log and skip — this directory is unwatched rather than crashing the watcher.
            # The dashboard's check_watch_limit surfaces the shortfall.
            logger.warning("inotify add_watch failed for %s: %s", path, exc)

    def _remove_watch_tree(self, root: str) -> None:
        prefix = root + os.sep
        doomed = [p for p in self._watches if p == root or p.startswith(prefix)]
        for path in doomed:
            watch = self._watches.pop(path)
            try:
                self._inotify.rm_watch(watch)
            except OSError:
                # When a watched dir is deleted the kernel auto-removes its watch
                # and emits IN_IGNORED; an explicit rm_watch then fails with
                # EINVAL. The watch is already gone, so this is safe to ignore.
                pass

    def _walk_add_watches(self, root: str) -> list[FsEvent]:
        """Add a watch for every non-ignored directory at/under `root`. Return
        synthetic `created` `FsEvent`s for every entry *below* `root` (the root
        itself is excluded — its own event, if any, is emitted by the caller).
        Used with an empty/discarded result at startup, and with its events
        yielded when a new subdirectory appears at runtime (attach-race close).
        """
        synthetic: list[FsEvent] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored dirs in place so os.walk never descends into them.
            dirnames[:] = [name for name in dirnames if name not in self._ignore_dirs]
            norm_dir = normalize_path(dirpath)
            if self._is_ignored(norm_dir):
                continue
            self._add_watch(norm_dir)
            if norm_dir != root:
                synthetic.append(FsEvent(norm_dir, FsEventType.created, is_dir=True))
            for name in filenames:
                fpath = normalize_path(os.path.join(dirpath, name))
                synthetic.append(FsEvent(fpath, FsEventType.created, is_dir=False))
        return synthetic

    # -- WatcherBackend protocol --------------------------------------------
    def set_roots(self, roots: set[str]) -> None:
        new_roots = {normalize_path(root) for root in roots}
        for gone in self._roots - new_roots:
            self._remove_watch_tree(gone)
        for added in new_roots - self._roots:
            if os.path.isdir(added) and not self._is_ignored(added):
                # Watch the existing tree but do NOT emit synthetic events for
                # pre-existing library content at startup.
                self._walk_add_watches(added)
        self._roots = new_roots

    async def events(self) -> AsyncIterator[FsEvent]:
        async for event in self._inotify:
            path_obj = event.path
            if path_obj is None:
                continue
            path = normalize_path(str(path_obj))
            if self._is_ignored(path):
                continue
            mask = int(event.mask)
            event_type = mask_to_event_type(mask)
            if event_type is None:
                continue
            yield FsEvent(path, event_type, is_dir=mask_is_dir(mask))

    async def aclose(self) -> None:
        self._inotify.close()
        self._watches.clear()
```

- [ ] **Step 5: Run tests to verify they pass (on Linux)**

Run: `python -m pytest tests/watcher/test_inotify_backend.py -v`
Expected (Linux): PASS (4 passed). On non-Linux: 4 skipped.

- [ ] **Step 6: Lint & type-check**

Run: `ruff check mediascanmonitor/watcher/inotify_backend.py tests/watcher/test_inotify_backend.py && mypy mediascanmonitor/watcher/inotify_backend.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/watcher/inotify_backend.py tests/watcher/test_inotify_backend.py pyproject.toml
git commit -m "feat(watcher): add InotifyBackend flat watching + mypy override"
```

---

## Task 7: `InotifyBackend` — recursive dynamic watch + attach-race rescan

**Files:**
- Modify: `mediascanmonitor/watcher/inotify_backend.py`
- Test: `tests/watcher/test_inotify_backend.py`

This task extends `events()` so that a **newly created** subdirectory is watched immediately and its existing contents are rescanned (synthetic `created` events), a deleted/moved-away directory has its watch subtree removed, and an **`IN_Q_OVERFLOW`** (kernel dropped events) triggers a resync across all roots (contract §8).

- [ ] **Step 1: Add the failing tests**

Append to `tests/watcher/test_inotify_backend.py`:

```python
async def test_new_subdir_is_watched_and_inner_file_emits_event(tmp_path: Path) -> None:
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        # Create a brand-new show folder *after* watching started.
        new_show = tmp_path / "NewShow"
        new_show.mkdir()
        subdir_event = await collect_event_for(agen, str(new_show))
        assert subdir_event.event_type is FsEventType.created
        assert subdir_event.is_dir is True

        # A file dropped into the new dir must produce an event -> the dynamic
        # watch attached. (Recursive dynamic watch behavior.)
        inner = new_show / "s01e01.mkv"
        inner.write_text("x")
        inner_event = await collect_event_for(agen, str(inner))

        assert inner_event.event_type is FsEventType.created
        assert inner_event.is_dir is False
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_new_subdir_rescan_emits_synthetic_created_for_existing_child(
    tmp_path: Path,
) -> None:
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        # Simulate the attach race: a directory that already contains a file is
        # moved into the watched root in one step (mkdir + file before we attach).
        staging = tmp_path.parent / f"{tmp_path.name}_staging"
        staging.mkdir()
        (staging / "preexisting.mkv").write_text("x")
        moved = tmp_path / "MovedShow"
        staging.rename(moved)  # appears as a single MOVED_TO (dir) on the root

        # The rescan must surface the file that existed before we could attach.
        event = await collect_event_for(agen, str(moved / "preexisting.mkv"))

        assert event.event_type is FsEventType.created
        assert event.is_dir is False
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_directory_deletion_removes_watches(tmp_path: Path) -> None:
    show = tmp_path / "OldShow"
    show.mkdir()
    backend = InotifyBackend(IGNORE)
    backend.set_roots({str(tmp_path)})
    agen = backend.events()
    try:
        # Confirm the dir is watched, then delete it.
        assert str(show) in backend._watches  # noqa: SLF001 - white-box check
        show.rmdir()

        event = await collect_event_for(agen, str(show))

        assert event.event_type is FsEventType.deleted
        assert event.is_dir is True
        # The watch for the removed directory must be gone.
        assert str(show) not in backend._watches  # noqa: SLF001
    finally:
        await agen.aclose()
        await backend.aclose()


async def test_add_watch_failure_is_logged_not_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Simulate the kernel watch limit (ENOSPC) being hit at add_watch time: the watcher
    # must log and degrade (leave the dir unwatched), never crash (contract §8).
    (tmp_path / "Show").mkdir()
    backend = InotifyBackend(IGNORE)

    def boom(path: object, mask: object) -> None:
        raise OSError(28, "No space left on device")  # errno 28 = ENOSPC

    monkeypatch.setattr(backend._inotify, "add_watch", boom)  # noqa: SLF001
    try:
        with caplog.at_level("WARNING"):
            backend.set_roots({str(tmp_path)})  # must NOT raise

        assert str(tmp_path) not in backend._watches  # noqa: SLF001 — degraded, not watched
        assert any("add_watch failed" in r.getMessage() for r in caplog.records)
    finally:
        await backend.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/watcher/test_inotify_backend.py -k "new_subdir or deletion_removes" -v`
Expected (Linux): the inner-file and rescan tests FAIL (no event arrives → `TimeoutError`, because the new subdir was never watched); the deletion test FAILS on the `not in backend._watches` assertion (watch never removed).

- [ ] **Step 3: Write the implementation**

Edit `mediascanmonitor/watcher/inotify_backend.py` — replace the `events()` method body with the directory-aware version:

```python
    async def events(self) -> AsyncIterator[FsEvent]:
        async for event in self._inotify:
            mask = int(event.mask)
            if mask & IN_Q_OVERFLOW:
                # Kernel dropped events (queue overflow). Re-attach watches across all
                # roots (a subdir whose CREATE was dropped is otherwise unwatched) and
                # re-emit their contents as synthetic `created` events so nothing is
                # silently missed. The per-scan_key debouncer collapses the burst.
                logger.warning("inotify queue overflow; resyncing watches under all roots")
                for root in sorted(self._roots):
                    for synthetic in self._walk_add_watches(root):
                        yield synthetic
                continue
            path_obj = event.path
            if path_obj is None:
                continue
            path = normalize_path(str(path_obj))
            if self._is_ignored(path):
                continue
            event_type = mask_to_event_type(mask)
            if event_type is None:
                continue
            is_dir = mask_is_dir(mask)

            if is_dir and event_type in (FsEventType.created, FsEventType.moved_to):
                # A new subdirectory appeared: attach watches across its subtree
                # and rescan its existing contents (closes the mkdir->add_watch
                # attach race). Duplicate events versus later real events are
                # harmless — the pipeline debounces per scan_key.
                yield FsEvent(path, event_type, is_dir=True)
                for synthetic in self._walk_add_watches(path):
                    yield synthetic
                continue

            if is_dir and event_type in (FsEventType.deleted, FsEventType.moved_from):
                self._remove_watch_tree(path)
                yield FsEvent(path, event_type, is_dir=True)
                continue

            yield FsEvent(path, event_type, is_dir=is_dir)
```

- [ ] **Step 4: Run tests to verify they pass (on Linux)**

Run: `python -m pytest tests/watcher/test_inotify_backend.py -v`
Expected (Linux): PASS (8 passed). On non-Linux: 8 skipped.

- [ ] **Step 5: Lint & type-check**

Run: `ruff check mediascanmonitor/watcher/ tests/watcher/ && mypy mediascanmonitor/watcher/inotify_backend.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/watcher/inotify_backend.py tests/watcher/test_inotify_backend.py
git commit -m "feat(watcher): recursive dynamic watch add/remove with attach-race rescan"
```

---

## Task 8: Whole-package verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full watcher test suite**

Run: `python -m pytest tests/watcher/ -v`
Expected: on Linux, all pass (5 + 8 + 7 + 8 = 28 tests). On non-Linux, the 8 inotify integration tests are skipped and the remaining 20 pass.

- [ ] **Step 2: Run the whole project test suite (no regressions)**

Run: `python -m pytest`
Expected: all collected tests pass (existing CLI/etc. tests unaffected).

- [ ] **Step 3: Lint & format check across the package**

Run: `ruff check mediascanmonitor/watcher/ tests/watcher/ && ruff format --check mediascanmonitor/watcher/ tests/watcher/`
Expected: no errors. If `ruff format --check` reports diffs, run `ruff format mediascanmonitor/watcher/ tests/watcher/` and re-commit.

- [ ] **Step 4: Strict type-check the package**

Run: `mypy mediascanmonitor/watcher/`
Expected: `Success: no issues found`.

- [ ] **Step 5: Final commit (only if Step 3 reformatted anything)**

```bash
git add -A
git commit -m "chore(watcher): formatting/lint pass for watcher package"
```

---

## Notes for the implementer

- **`InotifyBackend.events()` teardown:** `events()` is a plain async generator driven by the consumer. To stop it, the consumer either (a) cancels the task awaiting it, or (b) calls `agen.aclose()` (which throws `GeneratorExit` at the suspended `await`). The engine (sub-plan 06) will cancel its consuming task and then call `backend.aclose()`. In tests, always `await agen.aclose()` **before** `await backend.aclose()` so nothing is awaiting the inotify fd when it is closed.
- **`set_roots` does blocking `os.walk`.** This is intentional and acceptable: `set_roots` is called at startup and on config rebuild — *not* in the hot event loop. The per-event path (`events()`) does no blocking I/O except the cheap, synchronous `add_watch`/`rm_watch` ioctls when directories appear/disappear (unavoidable and microsecond-scale).
- **`add_watch` is idempotent:** asyncinotify returns the existing `Watch` for an already-watched path, so `_walk_add_watches` re-touching an attached directory is safe.
- **No extension filtering here.** Every matching create/move/delete is emitted regardless of extension; `pipeline/filters.py` (sub-plan 05) applies per-folder extension matching.
- **Duplicate events are expected and fine.** A file that lands in a freshly created directory may be reported both by the rescan (synthetic `created`) and by a subsequent real inotify event. The per-`scan_key` debouncer collapses them.
- **Queue overflow → resync (contract §8).** On `IN_Q_OVERFLOW` the backend logs a warning and re-walks all roots — re-attaching any watch whose `CREATE` was dropped and re-emitting their contents as synthetic `created` events, so a dropped event never becomes a permanently missed scan. Deterministically forcing a kernel queue overflow in a test is impractical; this path is covered by the logic plus the `IN_Q_OVERFLOW`-maps-to-`None` mask unit test (not an integration test). Exercise it manually with a large burst if you want belt-and-suspenders.
- **`add_watch` degradation (contract §8).** A runtime `add_watch` failure (kernel limit / `ENOSPC`) is logged and skipped — the directory is left unwatched rather than crashing the watcher; `check_watch_limit` surfaces the shortfall. Same testability caveat: exhausting the kernel limit on demand is impractical, so this is verified by the `try/except` logic, not an integration test.

---

## Self-Review

**1. Spec coverage (contract §8 + prompt scope):**
- `WatcherBackend` Protocol (`set_roots`/`events`/`aclose`) — Task 1. ✓ (signatures verbatim)
- `FakeWatcher` with full code, in `base.py`, supports `set_roots` + queue/list-fed `events()` — Task 1. ✓
- `WatchLimitStatus`, `read_max_user_watches(proc_path=...)`, `count_dirs`, `check_watch_limit(headroom=1.2)` — Tasks 2–4, signatures verbatim. ✓
- `InotifyBackend(ignore_dirs: frozenset[str])` implementing `WatcherBackend`; per-dir watch; subdir-create → add watch + rescan synthetic `created`; dir delete/moved_from → remove watches; mask→`FsEventType`; skip `ignore_dirs`; normalize paths — Tasks 5–7. ✓
- Pure mask→`FsEventType` helper, unit-tested without real inotify — Task 5. ✓
- Resilience (contract §8): `IN_Q_OVERFLOW` → log + resync (re-walk roots, synthetic `created`); runtime `add_watch` failure → log + skip, never raised — Tasks 5–7 (constant + mask test in Task 5; degradation in Task 6; overflow handling in Task 7). ✓
- "No extension filtering in the backend" — stated in module docstring + Notes. ✓
- UNIT tests run everywhere (watch_limit, FakeWatcher, mask mapping); INTEGRATION marked Linux-only via `pytestmark` skipif — Tasks 1–7. ✓
- Integration determinism via `asyncio.wait_for` draining, no arbitrary sleeps; negative case via `TimeoutError`/ordering — Task 6/7 + "Test markers & determinism". ✓
- mypy per-module override for asyncinotify added when first imported — Task 6 Step 1. ✓
- `from __future__ import annotations`, line length 100, mypy --strict, ruff — every file + verification Task 8. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to"/"write tests for the above" — every code and test step contains complete code. ✓

**3. Type/name consistency:** `mask_to_event_type`, `mask_is_dir`, `IN_CREATE/IN_MOVED_TO/IN_DELETE/IN_MOVED_FROM/IN_ISDIR`, `_walk_add_watches`, `_remove_watch_tree`, `_add_watch`, `_is_ignored`, `_watches`, `_roots`, `WatchLimitStatus(current, dirs, needed, recommended, ok)` are used identically across Tasks 4–8. `FsEvent(path, event_type, is_dir)` positional construction matches contract §5 field order. `FakeWatcher` API (`set_roots`, `feed`, `close_stream`, `events`, `aclose`, `roots`) is consistent between the implementation and its tests. ✓

**Contract deviations:** none. All public names and signatures match contract §8 verbatim; `IN_*` constants and the `mask_*` helpers are additive, asyncinotify-free internals (not contract symbols) and introduce no naming conflict.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-phase1-04-watcher.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
</content>
</invoke>
