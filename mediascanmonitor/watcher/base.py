"""Watcher backend protocol and a portable in-memory fake.

`WatcherBackend` is the only surface the engine depends on. `FakeWatcher` is a
queue-fed implementation that needs no inotify, so the pipeline/engine tests and
non-Linux development can drive `FsEvent`s deterministically.
"""

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
