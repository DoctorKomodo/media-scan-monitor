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
    await asyncio.sleep(0)
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
