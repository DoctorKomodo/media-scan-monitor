"""Shared validate→write→rebuild cores for server/folder mutations (contract §K).

Both the JSON ``/api/*`` routes (sub-plan 02) and the HTML ``/ui/*`` routes (sub-plan 04) call
these, so the two surfaces can never drift on the §D token-required check or the §F rebuild. Each
core does: token-required validation (servers) → off-thread Repo write (asyncio.to_thread, the repo
is sync SQLModel) → rebuild_engine. Folders carry no secret, so they skip the token check.
"""

import asyncio

from fastapi import HTTPException, status

from mediascanmonitor.db.models import Folder, Server, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.api_schemas import SERVER_TYPE_SPECS
from mediascanmonitor.web.rebuild import rebuild_engine


def _require_secret_or_422(server_type: ServerType, has_secret: bool) -> None:
    if SERVER_TYPE_SPECS[server_type].requires_secret and not has_secret:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"server type {server_type.value!r} requires a secret/token",
        )


async def apply_server_create(repo: Repo, engine: Engine, data: ServerCreate) -> Server:
    _require_secret_or_422(data.type, data.secret is not None and data.secret != "")
    server = await asyncio.to_thread(repo.create_server, data)
    await rebuild_engine(engine)
    return server


async def apply_server_update(
    repo: Repo, engine: Engine, server_id: int, data: ServerUpdate
) -> Server:
    existing = await asyncio.to_thread(repo.get_server, server_id)
    if existing is None:
        raise KeyError(f"server {server_id} not found")
    dumped = data.model_dump(exclude_unset=True)
    resulting_type = data.type if data.type is not None else existing.type
    if "secret" in dumped:
        # tri-state: explicit value (incl. None) decides; None/"" clears.
        resulting_has_secret = bool(dumped["secret"])
    else:
        resulting_has_secret = existing.secret_encrypted is not None
    _require_secret_or_422(resulting_type, resulting_has_secret)
    server = await asyncio.to_thread(repo.update_server, server_id, data)
    await rebuild_engine(engine)
    return server


async def apply_server_delete(repo: Repo, engine: Engine, server_id: int) -> None:
    await asyncio.to_thread(repo.delete_server, server_id)
    await rebuild_engine(engine)


async def apply_folder_create(
    repo: Repo, engine: Engine, server_id: int, data: FolderCreate
) -> Folder:
    created = await asyncio.to_thread(repo.create_folder, server_id, data)
    await rebuild_engine(engine)
    # repo.create_folder only force-loads `filetypes` when extensions were appended; for an
    # extension-less folder the relationship is unloaded on the committed/detached row, so
    # FolderRead.from_model(...) iterating it would raise DetachedInstanceError. Re-read via
    # get_folder (which force-loads filetypes) so every caller gets a fully-loaded folder.
    assert created.id is not None  # committed rows always carry an id
    folder = await asyncio.to_thread(repo.get_folder, created.id)
    assert folder is not None
    return folder


async def apply_folder_update(
    repo: Repo, engine: Engine, folder_id: int, data: FolderUpdate
) -> Folder:
    folder = await asyncio.to_thread(repo.update_folder, folder_id, data)
    await rebuild_engine(engine)
    return folder


async def apply_folder_delete(repo: Repo, engine: Engine, folder_id: int) -> None:
    await asyncio.to_thread(repo.delete_folder, folder_id)
    await rebuild_engine(engine)
