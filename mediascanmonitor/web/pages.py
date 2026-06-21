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

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.servers import registry
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS, ServerRead
from mediascanmonitor.web.deps import (
    get_engine,
    get_events_bus,
    get_repo,
    get_templates,
    require_page_auth,
)

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


def _scan_modes_by_type() -> dict[str, list[str]]:
    """Return {type value: sorted scan-mode values}.

    Drives the add-server form without literal type-name branching (invariant 6).
    """
    return {
        server_type.value: sorted(
            mode.value for mode in registry.get_adapter_class(server_type).supported_scan_modes
        )
        for server_type in ServerType
    }


def _type_specs() -> dict[str, dict[str, bool]]:
    """Serialize SERVER_TYPE_SPECS for the template/JS (the one place per-type rules live, §D)."""
    return {
        server_type.value: {
            "requires_secret": spec.requires_secret,
            "requires_base_url": spec.requires_base_url,
            "is_webhook": spec.is_webhook,
        }
        for server_type, spec in SERVER_TYPE_SPECS.items()
    }


async def _status_context(repo: Repo, engine: Engine) -> dict[str, Any]:
    """Shared dashboard/settings status context — same primitives as /api/status (§H)."""
    gate = await asyncio.to_thread(repo.get_setting, "inotify_gate")
    servers = await asyncio.to_thread(repo.list_servers)
    limit = engine.watch_limit
    return {
        "engine_state": engine.state.value,
        "inotify_gate": gate or "enforce",
        "watch": limit,  # WatchLimitStatus | None (.current/.dirs/.needed/.recommended/.ok)
        "server_count": len(servers),
        "enabled_server_count": sum(1 for s in servers if s.enabled),
    }


@router.get("/")
async def dashboard(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@router.get("/servers")
async def servers_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    servers = await asyncio.to_thread(repo.list_servers)
    return templates.TemplateResponse(
        request=request,
        name="servers.html",
        context={
            "servers": servers,
            "server_types": [t.value for t in ServerType],
            "scan_modes": [m.value for m in ScanMode],
            "debounce_modes": [m.value for m in DebounceMode],
            "type_specs": _type_specs(),
            "scan_modes_by_type": _scan_modes_by_type(),
        },
    )


async def _load_server_read(repo: Repo, server_id: int) -> ServerRead:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"server {server_id} not found")
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return ServerRead.from_model(server, folders)


@router.get("/servers/{server_id}")
async def server_detail(
    request: Request,
    server_id: int,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    server = await _load_server_read(repo, server_id)
    return templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={
            "server": server,
            "scan_modes": [m.value for m in ScanMode],
            "debounce_modes": [m.value for m in DebounceMode],
        },
    )


@router.get("/settings")
async def settings_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="settings.html", context=context)


@router.get("/events")
async def events_page(
    request: Request, templates: Jinja2Templates = Depends(get_templates)
) -> Response:
    return templates.TemplateResponse(request=request, name="events.html", context={})
