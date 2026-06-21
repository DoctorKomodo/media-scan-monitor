"""SSE /events/stream: auth-guarded; replays recent records + streams live as text/event-stream.

The stream is an INFINITE async generator. httpx's ASGITransport runs an ASGI app to completion,
so it deadlocks on an endless body — you cannot drive this endpoint's body through
``aclient.stream(...)``. We therefore test the auth/redirect via the sync TestClient and exercise
the streaming logic by driving ``_event_generator`` directly (pulling replay frames with ``anext``
then ``aclose()`` before the live loop, and using ``wait_for`` for the live path so a regression
fails fast instead of hanging).
"""

import asyncio
import json

import httpx
import pytest

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.web.pages import _event_generator, _sse_frame


def _make_record(server_name: str = "Plex Main") -> EventRecord:
    return EventRecord(
        ts="2026-06-20T18:30:00+00:00",
        server_id=1,
        server_name=server_name,
        scan_mode="targeted",
        scan_key="plex:1:/data/tv/Show",
        scan_path="/data/tv/Show",
        library_id="2",
        event_type="created",
        file_path="/data/tv/Show/ep01.mkv",
        ok=True,
        status_code=200,
        detail="scan queued",
    )


class _FakeRequest:
    """Minimal stand-in: ``_event_generator`` only awaits ``request.is_disconnected()``."""

    def __init__(self, *, disconnected: bool = False) -> None:
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


def test_events_stream_requires_auth(client: httpx.Client) -> None:
    resp = client.get("/events/stream", follow_redirects=False)
    assert resp.status_code == 303
    # The `client`/`app` fixtures set no password, so require_page_auth sends an anonymous user to
    # first-run setup (it only targets /login once a password exists — see 01's require_page_auth).
    assert resp.headers["location"] == "/setup"


def test_sse_frame_format_and_no_secret() -> None:
    frame = _sse_frame(_make_record())
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.removeprefix("data: ").strip())
    assert payload["server_name"] == "Plex Main"
    assert "secret" not in payload  # invariant 1: no token field ever in an SSE record
    assert "secret_encrypted" not in payload


async def test_sse_replays_recent_records(events_bus: EventsBus) -> None:
    # Records published before connecting are in recent() -> deterministic replay, no race.
    events_bus.publish(_make_record("Replayed One"))
    events_bus.publish(_make_record("Replayed Two"))

    gen = _event_generator(_FakeRequest(), events_bus)  # type: ignore[arg-type]
    # Two replay frames come from the `for record in bus.recent()` loop -> never blocks.
    frames = [await anext(gen), await anext(gen)]
    await gen.aclose()  # stop before the live subscribe() loop would await a new record

    parsed = [json.loads(f.removeprefix("data: ").strip()) for f in frames]
    assert [p["server_name"] for p in parsed] == ["Replayed One", "Replayed Two"]
    for frame in frames:
        assert frame.startswith("data: ") and frame.endswith("\n\n")
    for p in parsed:
        assert "secret" not in p


async def test_sse_streams_live_then_stops_on_disconnect(events_bus: EventsBus) -> None:
    request = _FakeRequest(disconnected=False)
    gen = _event_generator(request, events_bus)  # type: ignore[arg-type]  # recent() is empty

    # Enter the live subscribe() loop, then publish so the subscriber queue receives it.
    pending = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)  # let the generator register its subscriber queue
    events_bus.publish(_make_record("Live Server"))
    frame = await asyncio.wait_for(pending, timeout=1.0)
    assert json.loads(frame.removeprefix("data: ").strip())["server_name"] == "Live Server"

    # Once the client disconnects, the next iteration breaks -> generator ends.
    request.disconnected = True
    events_bus.publish(_make_record("After Disconnect"))
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(gen), timeout=1.0)
    await gen.aclose()
