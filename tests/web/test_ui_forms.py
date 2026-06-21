"""/ui form handlers: success swaps a partial, 422 renders inline, the write-core rebuilds."""

import httpx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate


def _seed_plex(repo) -> int:  # type: ignore[no-untyped-def]
    s = repo.create_server(
        ServerCreate(name="Plex", type=ServerType.plex, base_url="http://plex:32400", secret="tok")
    )
    return int(s.id)


def test_ui_create_webhook_server_swaps_list_and_rebuilds(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    before = engine.rebuild_calls
    resp = auth_client.post(
        "/ui/servers",
        data={
            "name": "Hook",
            "type": "webhook",
            "base_url": "https://hook.example",
            "scan_mode": "library",
            "debounce_mode": "off",
            "debounce_window_seconds": "30",
            "retry_attempts": "1",
            "timeout_seconds": "10",
        },
    )
    assert resp.status_code == 200
    assert "Hook" in resp.text  # the new server row is in the swapped list
    assert engine.rebuild_calls == before + 1
    assert any(s.name == "Hook" for s in repo.list_servers())


def test_ui_create_plex_without_token_renders_inline_error_no_rebuild(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    before = engine.rebuild_calls
    resp = auth_client.post(
        "/ui/servers",
        data={
            "name": "BadPlex",
            "type": "plex",
            "base_url": "http://plex:32400",
            "scan_mode": "targeted",
            "debounce_mode": "trailing",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
        },
    )
    assert resp.status_code == 200  # softened so htmx swaps the message
    assert "token" in resp.text.lower()
    assert resp.headers.get("hx-retarget") == "#form-error"
    assert engine.rebuild_calls == before  # core raised before writing/rebuilding
    assert not any(s.name == "BadPlex" for s in repo.list_servers())


def test_ui_update_server_keeps_secret_when_blank_and_rebuilds(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    before = engine.rebuild_calls
    resp = auth_client.post(
        f"/ui/servers/{sid}/update",
        data={
            "name": "Plex",
            "base_url": "http://plex:32400",
            "secret": "",
            "scan_mode": "targeted",
            "debounce_mode": "trailing",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
            "verify_tls": "on",
            "enabled": "on",
        },
    )
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
    assert repo.get_server(sid).secret_encrypted is not None  # blank secret left the token intact


def test_ui_delete_server_swaps_list_and_rebuilds(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    before = engine.rebuild_calls
    resp = auth_client.post(f"/ui/servers/{sid}/delete")
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
    assert repo.get_server(sid) is None


def test_ui_create_and_delete_folder(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    resp = auth_client.post(
        f"/ui/servers/{sid}/folders",
        data={"path": "/data/tv", "library_id": "2", "extensions": "mkv, mp4", "enabled": "on"},
    )
    assert resp.status_code == 200
    assert "/data/tv" in resp.text
    folders = repo.list_folders(sid)
    assert len(folders) == 1
    fid = folders[0].id

    resp2 = auth_client.post(f"/ui/folders/{fid}/delete")
    assert resp2.status_code == 200
    assert repo.list_folders(sid) == []


def test_ui_update_folder_replaces_extensions(
    auth_client: httpx.Client,
    repo,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    repo.create_folder(sid, FolderCreate(path="/data/tv", library_id="2", extensions=["mkv"]))
    fid = repo.list_folders(sid)[0].id
    resp = auth_client.post(
        f"/ui/folders/{fid}/update",
        data={"path": "/data/tv", "library_id": "2", "extensions": "mp4", "enabled": "on"},
    )
    assert resp.status_code == 200
    exts = [ft.extension for ft in repo.list_folders(sid)[0].filetypes]
    assert exts == ["mp4"]
