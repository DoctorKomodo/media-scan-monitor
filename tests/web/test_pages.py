"""Page routes: 200 for an authed client, 303 redirect for an anon client, key content present."""

import httpx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate
from mediascanmonitor.engine import EngineState
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus


def _seed_server(repo) -> int:  # type: ignore[no-untyped-def]
    server = repo.create_server(
        ServerCreate(
            name="Plex Main", type=ServerType.plex, base_url="http://plex:32400", secret="tok"
        )
    )
    repo.create_folder(server.id, FolderCreate(path="/data/tv", library_id="2", extensions=["mkv"]))
    return int(server.id)


def test_dashboard_redirects_when_anon(client: httpx.Client) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    # No password set on the `client` fixture → require_page_auth routes to first-run /setup
    # (it targets /login only once a password exists).
    assert resp.headers["location"] == "/setup"


def test_dashboard_renders_engine_and_watch_status(auth_client: httpx.Client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.state = EngineState.blocked
    engine.watch_limit = WatchLimitStatus(
        current=8192, dirs=20000, needed=24000, recommended=28800, ok=False
    )
    resp = auth_client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "blocked" in body
    assert "28800" in body  # recommended kernel ceiling surfaced
    assert "max_user_watches" in body  # the recommended sysctl line


def test_servers_page_lists_servers_and_add_form(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    _seed_server(repo)
    resp = auth_client.get("/servers")
    assert resp.status_code == 200
    assert "Plex Main" in resp.text
    assert 'name="type"' in resp.text  # the add-server form is present


def test_server_detail_shows_folders_and_test_button(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    sid = _seed_server(repo)
    resp = auth_client.get(f"/servers/{sid}")
    assert resp.status_code == 200
    assert "/data/tv" in resp.text
    assert "Test" in resp.text  # Test button present


def test_server_detail_404_for_missing(auth_client: httpx.Client) -> None:
    assert auth_client.get("/servers/9999").status_code == 404


def test_settings_page_has_gate_toggle(auth_client: httpx.Client) -> None:
    resp = auth_client.get("/settings")
    assert resp.status_code == 200
    assert 'name="inotify_gate"' in resp.text
    assert "Re-check" in resp.text


def test_events_page_opens_stream(auth_client: httpx.Client) -> None:
    resp = auth_client.get("/events")
    assert resp.status_code == 200
    assert "/events/stream" in resp.text  # the page wires the SSE source


def test_pages_redirect_when_anon(client: httpx.Client) -> None:
    for path in ("/servers", "/settings", "/events"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
