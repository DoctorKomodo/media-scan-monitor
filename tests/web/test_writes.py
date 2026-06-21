"""Shared write-cores: token-required 422 + rebuild-on-write (contract §K/§D)."""

import pytest
from fastapi import HTTPException

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate
from mediascanmonitor.engine import Engine
from mediascanmonitor.web.writes import (
    apply_folder_create,
    apply_server_create,
    apply_server_delete,
    apply_server_update,
)


async def test_create_rejects_missing_secret_for_auth_type(repo: Repo, engine: Engine) -> None:
    with pytest.raises(HTTPException) as exc:
        await apply_server_create(repo, engine, ServerCreate(name="plex", type=ServerType.plex))
    assert exc.value.status_code == 422


async def test_create_webhook_without_secret_is_allowed(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="hook", type=ServerType.webhook)
    )
    assert server.id is not None
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_create_calls_rebuild(repo: Repo, engine: Engine) -> None:
    await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_update_missing_secret_clear_rejected(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert server.id is not None
    with pytest.raises(HTTPException) as exc:
        await apply_server_update(repo, engine, server.id, ServerUpdate(secret=None))
    assert exc.value.status_code == 422


async def test_update_omitted_secret_keeps_existing(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert server.id is not None
    updated = await apply_server_update(repo, engine, server.id, ServerUpdate(enabled=False))
    assert updated.enabled is False  # no 422: existing secret still present


async def test_update_missing_server_raises_keyerror(repo: Repo, engine: Engine) -> None:
    with pytest.raises(KeyError):
        await apply_server_update(repo, engine, 999, ServerUpdate(enabled=False))


async def test_delete_calls_rebuild_even_when_absent(repo: Repo, engine: Engine) -> None:
    await apply_server_delete(repo, engine, 999)  # idempotent delete
    assert engine.rebuild_calls == 1  # type: ignore[attr-defined]


async def test_folder_create_calls_rebuild(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="hook", type=ServerType.webhook)
    )
    assert server.id is not None
    before = engine.rebuild_calls  # type: ignore[attr-defined]
    folder = await apply_folder_create(repo, engine, server.id, FolderCreate(path="/data/tv"))
    assert folder.id is not None
    assert engine.rebuild_calls == before + 1  # type: ignore[attr-defined]
