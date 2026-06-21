"""System & status surface (contract §H).

Liveness (`/health`, unauth — the UI stays reachable even when the engine is blocked),
readiness (`/ready`, auth — 200 iff DB reachable AND engine running), status
(`/api/status`), and the user-facing gate controls (`PUT /api/settings/inotify-gate`,
`POST /api/engine/recheck`). All repo work runs off the loop via ``asyncio.to_thread``.
No secret is ever read or returned here (invariant 1).
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine, EngineState
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.rebuild import rebuild_engine


class StatusRead(BaseModel):
    engine_state: str  # EngineState value: starting|running|blocked|stopped
    inotify_gate: str  # "enforce" | "off"
    watch_current: int | None
    watch_dirs: int | None
    watch_needed: int | None
    watch_recommended: int | None
    watch_ok: bool | None
    server_count: int
    enabled_server_count: int


class InotifyGateUpdate(BaseModel):
    inotify_gate: Literal["enforce", "off"]


async def build_status(repo: Repo, engine: Engine) -> StatusRead:
    gate = await asyncio.to_thread(repo.get_setting, "inotify_gate")
    all_servers = await asyncio.to_thread(repo.list_servers)
    enabled_servers = await asyncio.to_thread(repo.list_servers, enabled_only=True)
    wl = engine.watch_limit
    return StatusRead(
        engine_state=engine.state.value,
        inotify_gate=gate or "enforce",
        watch_current=wl.current if wl else None,
        watch_dirs=wl.dirs if wl else None,
        watch_needed=wl.needed if wl else None,
        watch_recommended=wl.recommended if wl else None,
        watch_ok=wl.ok if wl else None,
        server_count=len(all_servers),
        enabled_server_count=len(enabled_servers),
    )


# --- liveness: unauthenticated (allow-list, contract §B) --------------------
health_router = APIRouter()


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- readiness/status/controls: authenticated -------------------------------
router = APIRouter(dependencies=[Depends(require_api_auth)])


@router.get("/ready")
async def ready(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> JSONResponse:
    try:
        await asyncio.to_thread(repo.get_setting, "inotify_gate")  # DB reachability probe
    except Exception:
        return JSONResponse({"status": "db unreachable"}, status_code=503)
    if engine.state is EngineState.running:
        return JSONResponse({"status": "ready"}, status_code=200)
    return JSONResponse({"status": engine.state.value}, status_code=503)


@router.get("/api/status", response_model=StatusRead)
async def api_status(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    return await build_status(repo, engine)


@router.put("/api/settings/inotify-gate", response_model=StatusRead)
async def set_inotify_gate(
    body: InotifyGateUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    await asyncio.to_thread(repo.set_setting, "inotify_gate", body.inotify_gate)
    # flipping to "off" recovers a blocked engine (§I blocked->running)
    await rebuild_engine(engine)
    return await build_status(repo, engine)


@router.post("/api/engine/recheck", response_model=StatusRead)
async def engine_recheck(
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> StatusRead:
    await rebuild_engine(engine)  # re-evaluate the gate after an out-of-band kernel-limit change
    return await build_status(repo, engine)
