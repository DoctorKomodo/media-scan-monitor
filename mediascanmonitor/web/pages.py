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
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventRecord, EventsBus
from mediascanmonitor.servers import registry
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS, FolderRead, ServerRead
from mediascanmonitor.web.deps import (
    get_engine,
    get_events_bus,
    get_repo,
    get_templates,
    require_page_auth,
)
from mediascanmonitor.web.rebuild import rebuild_engine
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_folder_delete,
    apply_folder_update,
    apply_server_create,
    apply_server_delete,
    apply_server_update,
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


# ---------------------------------------------------------------------------
# /ui form handlers — HTML-partial twins of the JSON /api (contract §J / §K)
# ---------------------------------------------------------------------------


def _split_extensions(raw: str) -> list[str]:
    """Parse a comma/whitespace-separated extensions field into a list.

    Validators in FolderCreate/FolderUpdate normalize and deduplicate further.
    """
    return [part for part in re.split(r"[,\s]+", raw.strip()) if part]


def _error_partial(
    request: Request, templates: Jinja2Templates, message: str, target: str
) -> Response:
    """Render the inline error partial, retargeted (via htmx headers) into the form's error slot.

    Returns status 200 (htmx only swaps 2xx); the JSON /api surface keeps the real 422.
    """
    response = templates.TemplateResponse(
        request=request, name="_error.html", context={"message": message}
    )
    response.headers["HX-Retarget"] = target
    response.headers["HX-Reswap"] = "innerHTML"
    return response


async def _servers_list_response(
    request: Request, repo: Repo, templates: Jinja2Templates
) -> Response:
    servers = await asyncio.to_thread(repo.list_servers)
    return templates.TemplateResponse(
        request=request, name="_servers_list.html", context={"servers": servers}
    )


async def _folders_response(
    request: Request, repo: Repo, server_id: int, templates: Jinja2Templates
) -> Response:
    raw = await asyncio.to_thread(repo.list_folders, server_id)
    folders = [FolderRead.from_model(f) for f in raw]
    return templates.TemplateResponse(
        request=request, name="_folders.html", context={"folders": folders}
    )


@router.post("/ui/servers")
async def ui_create_server(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    base_url: str = Form(""),
    secret: str = Form(""),
    scan_mode: str = Form(...),
    debounce_mode: str = Form(...),
    debounce_window_seconds: int = Form(30),
    retry_attempts: int = Form(3),
    timeout_seconds: float = Form(10.0),
    verify_tls: bool = Form(False),
    enabled: bool = Form(False),
    webhook_method: str = Form(""),
    webhook_headers_json: str = Form(""),
    webhook_body_template: str = Form(""),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Build the schema INSIDE the try: an invalid enum string (ServerType/ScanMode/DebounceMode)
    # or a pydantic field-validator failure raises ValueError, which must render the inline error
    # partial — not bubble to a 500. (Pydantic's ValidationError is a ValueError subclass.)
    try:
        data = ServerCreate(
            name=name,
            type=ServerType(type),
            base_url=base_url,
            secret=secret or None,
            scan_mode=ScanMode(scan_mode),
            debounce_mode=DebounceMode(debounce_mode),
            debounce_window_seconds=debounce_window_seconds,
            retry_attempts=retry_attempts,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
            enabled=enabled,
            webhook_method=webhook_method or None,
            webhook_headers_json=webhook_headers_json or None,
            webhook_body_template=webhook_body_template or None,
        )
        await apply_server_create(repo, engine, data)
    except HTTPException as exc:
        return _error_partial(request, templates, str(exc.detail), "#form-error")
    except ValueError as exc:
        return _error_partial(request, templates, str(exc), "#form-error")
    return await _servers_list_response(request, repo, templates)


@router.post("/ui/servers/{server_id}/update")
async def ui_update_server(
    request: Request,
    server_id: int,
    name: str = Form(...),
    base_url: str = Form(""),
    secret: str = Form(""),
    clear_secret: bool = Form(False),
    scan_mode: str = Form(...),
    debounce_mode: str = Form(...),
    debounce_window_seconds: int = Form(30),
    retry_attempts: int = Form(3),
    timeout_seconds: float = Form(10.0),
    verify_tls: bool = Form(False),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Build the schema INSIDE the try: enum/validator failures → ValueError → inline error, not 500.
    # apply_server_update raises KeyError if the server was deleted concurrently — render the
    # error partial rather than 500 (mirrors the /api twin translating KeyError → 404).
    try:
        # Secret tri-state via exclude_unset: omit when blank (keep), set None when "clear" ticked.
        fields: dict[str, Any] = {
            "name": name,
            "base_url": base_url,
            "scan_mode": ScanMode(scan_mode),
            "debounce_mode": DebounceMode(debounce_mode),
            "debounce_window_seconds": debounce_window_seconds,
            "retry_attempts": retry_attempts,
            "timeout_seconds": timeout_seconds,
            "verify_tls": verify_tls,
            "enabled": enabled,
        }
        if clear_secret:
            fields["secret"] = None
        elif secret:
            fields["secret"] = secret
        data = ServerUpdate(**fields)
        await apply_server_update(repo, engine, server_id, data)
    except HTTPException as exc:
        return _error_partial(request, templates, str(exc.detail), "#edit-error")
    except (ValueError, KeyError) as exc:
        return _error_partial(request, templates, str(exc), "#edit-error")
    return templates.TemplateResponse(
        request=request, name="_error.html", context={"message": "Saved."}
    )


@router.post("/ui/servers/{server_id}/delete")
async def ui_delete_server(
    request: Request,
    server_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    try:
        await apply_server_delete(repo, engine, server_id)
    except (HTTPException, ValueError, KeyError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#edit-error")
    return await _servers_list_response(request, repo, templates)


@router.post("/ui/servers/{server_id}/folders")
async def ui_create_folder(
    request: Request,
    server_id: int,
    path: str = Form(...),
    library_id: str = Form(""),
    extensions: str = Form(""),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    try:
        data = FolderCreate(
            path=path,
            library_id=library_id or None,
            extensions=_split_extensions(extensions),
            enabled=enabled,
        )
        await apply_folder_create(repo, engine, server_id, data)
    except (HTTPException, ValueError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#folder-error")
    return await _folders_response(request, repo, server_id, templates)


@router.post("/ui/folders/{folder_id}/update")
async def ui_update_folder(
    request: Request,
    folder_id: int,
    path: str = Form(...),
    library_id: str = Form(""),
    extensions: str = Form(""),
    enabled: bool = Form(False),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None:
        return _error_partial(request, templates, f"folder {folder_id} not found", "#folder-error")
    server_id = folder.server_id
    try:
        data = FolderUpdate(
            path=path,
            library_id=library_id or None,
            extensions=_split_extensions(extensions),
            enabled=enabled,
        )
        await apply_folder_update(repo, engine, folder_id, data)
    except (HTTPException, ValueError, KeyError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#folder-error")
    return await _folders_response(request, repo, server_id, templates)


@router.post("/ui/folders/{folder_id}/delete")
async def ui_delete_folder(
    request: Request,
    folder_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None:
        return _error_partial(request, templates, f"folder {folder_id} not found", "#folder-error")
    server_id = folder.server_id
    try:
        await apply_folder_delete(repo, engine, folder_id)
    except (HTTPException, ValueError, KeyError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _error_partial(request, templates, detail, "#folder-error")
    return await _folders_response(request, repo, server_id, templates)


_INOTIFY_GATE_VALUES = frozenset({"enforce", "off"})


@router.post("/ui/settings")
async def ui_settings(
    request: Request,
    inotify_gate: str = Form(...),
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if inotify_gate not in _INOTIFY_GATE_VALUES:
        raise HTTPException(status_code=422, detail="inotify_gate must be 'enforce' or 'off'")
    await asyncio.to_thread(repo.set_setting, "inotify_gate", inotify_gate)
    await rebuild_engine(engine)  # flipping to off can recover a blocked engine (§H/§I)
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="_status.html", context=context)


@router.post("/ui/recheck")
async def ui_recheck(
    request: Request,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    await rebuild_engine(engine)  # re-evaluate the gate after an out-of-band host limit change (§H)
    context = await _status_context(repo, engine)
    return templates.TemplateResponse(request=request, name="_status.html", context=context)
