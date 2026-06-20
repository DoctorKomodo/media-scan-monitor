"""Integration tests for the real asyncinotify backend (Linux-only).

Determinism: we never sleep for a fixed period. inotify queues events in the
kernel, so each test performs a filesystem operation and then pulls the next
event(s) from the async iterator with a bounded `asyncio.wait_for`. The
ignore-dir test asserts the *absence* of an event via `TimeoutError`.
"""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mediascanmonitor.pipeline.events import FsEvent, FsEventType
from mediascanmonitor.watcher.inotify_backend import InotifyBackend

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="inotify backend is Linux-only")

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
        assert str(show) in backend._watches  # white-box check
        show.rmdir()

        event = await collect_event_for(agen, str(show))

        assert event.event_type is FsEventType.deleted
        assert event.is_dir is True
        # The watch for the removed directory must be gone.
        assert str(show) not in backend._watches  # watch removed on dir deletion
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

    def boom(self: object, path: object, mask: object) -> None:
        raise OSError(28, "No space left on device")  # errno 28 = ENOSPC

    # asyncinotify's Inotify uses __slots__, so add_watch cannot be patched on the
    # instance (read-only); patch the class with a self-bearing stub instead.
    # monkeypatch auto-reverts the class attribute after the test.
    monkeypatch.setattr(type(backend._inotify), "add_watch", boom)
    try:
        with caplog.at_level("WARNING"):
            backend.set_roots({str(tmp_path)})  # must NOT raise

        assert str(tmp_path) not in backend._watches  # degraded, not watched
        assert any("add_watch failed" in r.getMessage() for r in caplog.records)
    finally:
        await backend.aclose()
