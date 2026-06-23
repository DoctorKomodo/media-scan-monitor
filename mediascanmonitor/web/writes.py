"""Shared validate→write→rebuild cores for server/folder mutations (contract §K).

Both the JSON ``/api/*`` routes (sub-plan 02) and the HTML ``/ui/*`` routes (sub-plan 04) call
these, so the two surfaces can never drift on the §D token-required check or the §F rebuild. Each
core does: token-required validation (servers) → off-thread Repo write (asyncio.to_thread, the repo
is sync SQLModel) → rebuild_engine. Folders carry no secret, so they skip the token check.

The ``*_with_folders`` create/update pair are parallel twins — change both together so the
add-server and edit-server flows stay aligned.
"""

import asyncio

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

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


def _name_conflict(name: str) -> HTTPException:
    """Translate the create path's IntegrityError into a 409 every surface can render.

    The only uniqueness constraint on Server is its name, so a create-time IntegrityError
    means a duplicate name. Mapping it here (not per-route) keeps /api and /ui consistent
    instead of one 500-ing while the other shows a friendly message.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"A server named {name!r} already exists.",
    )


def _require_update_secret(data: ServerUpdate, existing: Server) -> None:
    """Secret-required (§D) gate for an update — shared by both update cores so they can't drift.

    The resulting type/secret derive from the submitted fields plus the existing row: a secret
    present in the dump (tri-state — an explicit value, incl. ``None``/``""``, decides) sets it;
    an omitted secret keeps whatever the row already has.
    """
    dumped = data.model_dump(exclude_unset=True)
    resulting_type = data.type if data.type is not None else existing.type
    if "secret" in dumped:
        resulting_has_secret = bool(dumped["secret"])
    else:
        resulting_has_secret = existing.secret_encrypted is not None
    _require_secret_or_422(resulting_type, resulting_has_secret)


async def apply_server_create(repo: Repo, engine: Engine, data: ServerCreate) -> Server:
    _require_secret_or_422(data.type, data.secret is not None and data.secret != "")
    try:
        server = await asyncio.to_thread(repo.create_server, data)
    except IntegrityError as exc:
        raise _name_conflict(data.name) from exc
    await rebuild_engine(engine)
    return server


async def apply_server_create_with_folders(
    repo: Repo, engine: Engine, server_data: ServerCreate, folders: list[FolderCreate]
) -> Server:
    """Create a server and its folders atomically, then rebuild once.

    Same token-required gate as apply_server_create; the single transactional repo write
    means a rejected/duplicate server never leaves orphan folders. One rebuild covers both.
    """
    _require_secret_or_422(
        server_data.type, server_data.secret is not None and server_data.secret != ""
    )
    try:
        server = await asyncio.to_thread(repo.create_server_with_folders, server_data, folders)
    except IntegrityError as exc:
        raise _name_conflict(server_data.name) from exc
    await rebuild_engine(engine)
    return server


async def apply_server_update(
    repo: Repo, engine: Engine, server_id: int, data: ServerUpdate
) -> Server:
    existing = await asyncio.to_thread(repo.get_server, server_id)
    if existing is None:
        raise KeyError(f"server {server_id} not found")
    _require_update_secret(data, existing)
    server = await asyncio.to_thread(repo.update_server, server_id, data)
    await rebuild_engine(engine)
    return server


async def apply_server_update_with_folders(
    repo: Repo,
    engine: Engine,
    server_id: int,
    data: ServerUpdate,
    folders: list[FolderCreate],
) -> Server:
    """Update a server and replace its folder set atomically, then rebuild once.

    The detail page's single "Save changes" persists the server fields and the whole folder
    list in one request. Same secret-required gate as apply_server_update; a duplicate-name
    rename raises IntegrityError, translated to a 409 (mirroring apply_server_create_with_folders)
    so the UI shows an inline error instead of a 500. Folders carry no secret. Twin of
    apply_server_create_with_folders — keep the two in lockstep.
    """
    existing = await asyncio.to_thread(repo.get_server, server_id)
    if existing is None:
        raise KeyError(f"server {server_id} not found")
    _require_update_secret(data, existing)
    try:
        server = await asyncio.to_thread(repo.update_server_with_folders, server_id, data, folders)
    except IntegrityError as exc:
        raise _name_conflict(data.name or existing.name) from exc
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
