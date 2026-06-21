"""Browser UI: server-rendered pages, htmx /ui form handlers, and the SSE event stream.

All routes are guarded by ``require_page_auth`` at router level (contract §B): unauthenticated
requests get a 303 redirect to /login (or /setup when no password is set). The only
unauthenticated web surface — /login, /setup, /static/* — is owned elsewhere (01 / StaticFiles).

The /ui/* mutations are thin presentations of the SAME write as /api/*: they parse
``Form(...)``, build the existing write-schemas, and call the shared write-cores in
``web/writes.py`` (contract §J), so they validate (incl. the §D token-required 422), write
off-thread, and ``rebuild_engine`` identically to the JSON API. They differ only in input
parsing and HTML-partial output (invariant 4).

SSE (contract §K): a plain ``StreamingResponse(media_type="text/event-stream")`` over an async
generator that replays ``bus.recent()`` then yields ``bus.subscribe()`` frames as
``data: {json}\\n\\n``, breaking on ``await request.is_disconnected()``. No sse-starlette.
Known, accepted race: a record published between the recent() snapshot and subscribe()
registration may be missed or duplicated.
"""

import dataclasses
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.web.deps import get_events_bus, require_page_auth

router = APIRouter(dependencies=[Depends(require_page_auth)])


def _sse_frame(record: EventRecord) -> str:
    """Serialize a (secret-free) EventRecord as one SSE ``data:`` frame."""
    return f"data: {json.dumps(dataclasses.asdict(record))}\n\n"


async def _event_generator(request: Request, bus: EventsBus) -> AsyncIterator[str]:
    for record in bus.recent():
        yield _sse_frame(record)
    async for record in bus.subscribe():
        if await request.is_disconnected():
            break
        yield _sse_frame(record)


@router.get("/events/stream")
async def events_stream(
    request: Request, bus: EventsBus = Depends(get_events_bus)
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(request, bus),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
