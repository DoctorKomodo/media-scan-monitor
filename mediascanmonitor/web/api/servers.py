"""/api/servers JSON CRUD + connectivity test (contract §D).

Reads go through the redacted ServerRead; writes through the shared write-cores (web/writes.py),
so the two surfaces can never drift on the §D token-required check or the §F rebuild. The test
endpoint builds a throwaway ServerRuntime from the stored row (secret decrypted in memory only
via resolve_secret), constructs the registered adapter, awaits adapter.test(), and ALWAYS closes
its client.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.servers.http import build_client
from mediascanmonitor.servers.registry import create_adapter
from mediascanmonitor.web.api_schemas import ServerRead, ServerTestResponse
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.writes import (
    apply_server_create,
    apply_server_delete,
    apply_server_update,
)

router = APIRouter(
    prefix="/api/servers",
    tags=["servers"],
    dependencies=[Depends(require_api_auth)],
)


async def _read_server(repo: Repo, server_id: int) -> ServerRead:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return ServerRead.from_model(server, folders)


@router.get("")
async def list_servers(repo: Repo = Depends(get_repo)) -> list[ServerRead]:
    servers = await asyncio.to_thread(repo.list_servers)
    out: list[ServerRead] = []
    for server in servers:
        assert server.id is not None
        folders = await asyncio.to_thread(repo.list_folders, server.id)
        out.append(ServerRead.from_model(server, folders))
    return out


@router.get("/{server_id}")
async def get_server(server_id: int, repo: Repo = Depends(get_repo)) -> ServerRead:
    return await _read_server(repo, server_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(
    data: ServerCreate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> ServerRead:
    server = await apply_server_create(repo, engine, data)
    assert server.id is not None
    return await _read_server(repo, server.id)


@router.patch("/{server_id}")
async def update_server(
    server_id: int,
    data: ServerUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> ServerRead:
    try:
        await apply_server_update(repo, engine, server_id, data)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found") from None
    return await _read_server(repo, server_id)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> None:
    await apply_server_delete(repo, engine, server_id)


@router.post("/{server_id}/test")
async def test_server(server_id: int, repo: Repo = Depends(get_repo)) -> ServerTestResponse:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    assert server.id is not None
    secret = await asyncio.to_thread(repo.resolve_secret, server)
    runtime = ServerRuntime(
        server_id=server.id,
        name=server.name,
        type=server.type,
        base_url=server.base_url,
        verify_tls=server.verify_tls,
        timeout_seconds=server.timeout_seconds,
        secret=secret,
        scan_mode=server.scan_mode,
        debounce_mode=server.debounce_mode,
        debounce_window_seconds=server.debounce_window_seconds,
        retry_attempts=server.retry_attempts,
        webhook_method=server.webhook_method,
        webhook_headers_json=server.webhook_headers_json,
        webhook_body_template=server.webhook_body_template,
    )
    client = build_client(verify_tls=server.verify_tls, timeout_seconds=server.timeout_seconds)
    try:
        adapter = create_adapter(runtime, client)
        result = await adapter.test()
    finally:
        await client.aclose()
    return ServerTestResponse(ok=result.ok, detail=result.detail)
