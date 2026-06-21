"""/api/servers/{server_id}/folders JSON CRUD (contract §E).

Folder mutations carry no secret, so the write-cores skip the token check. The parent server is
verified for every route (404 if absent), and a folder fetched for a server it does not belong to is
treated as not-found so ids cannot leak across servers.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.api_schemas import FolderRead
from mediascanmonitor.web.deps import get_engine, get_repo, require_api_auth
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_folder_delete,
    apply_folder_update,
)

router = APIRouter(
    prefix="/api/servers/{server_id}/folders",
    tags=["folders"],
    dependencies=[Depends(require_api_auth)],
)


async def _require_server(repo: Repo, server_id: int) -> None:
    server = await asyncio.to_thread(repo.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")


async def _owned_folder(repo: Repo, server_id: int, folder_id: int) -> None:
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None or folder.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "folder not found")


@router.get("")
async def list_folders(server_id: int, repo: Repo = Depends(get_repo)) -> list[FolderRead]:
    await _require_server(repo, server_id)
    folders = await asyncio.to_thread(repo.list_folders, server_id)
    return [FolderRead.from_model(f) for f in folders]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(
    server_id: int,
    data: FolderCreate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> FolderRead:
    await _require_server(repo, server_id)
    folder = await apply_folder_create(repo, engine, server_id, data)
    return FolderRead.from_model(folder)


@router.get("/{folder_id}")
async def get_folder(server_id: int, folder_id: int, repo: Repo = Depends(get_repo)) -> FolderRead:
    await _require_server(repo, server_id)
    folder = await asyncio.to_thread(repo.get_folder, folder_id)
    if folder is None or folder.server_id != server_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "folder not found")
    return FolderRead.from_model(folder)


@router.patch("/{folder_id}")
async def update_folder(
    server_id: int,
    folder_id: int,
    data: FolderUpdate,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> FolderRead:
    await _require_server(repo, server_id)
    await _owned_folder(repo, server_id, folder_id)
    folder = await apply_folder_update(repo, engine, folder_id, data)
    return FolderRead.from_model(folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    server_id: int,
    folder_id: int,
    repo: Repo = Depends(get_repo),
    engine: Engine = Depends(get_engine),
) -> None:
    await _require_server(repo, server_id)
    await _owned_folder(repo, server_id, folder_id)
    await apply_folder_delete(repo, engine, folder_id)
