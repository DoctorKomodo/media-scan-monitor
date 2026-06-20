"""Unit tests for the FakeWatcher test double (runs on every platform)."""

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
