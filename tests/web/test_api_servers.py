"""/api/servers CRUD + test endpoint (contract §D)."""

import httpx
import respx
from starlette.testclient import TestClient

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/servers").status_code == 401


def test_list_empty(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/servers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_then_get_redacts_secret(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/api/servers",
        json={"name": "plex", "type": "plex", "base_url": "http://p:32400", "secret": "tok"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_secret"] is True
    assert "secret" not in body
    assert "tok" not in resp.text
    server_id = body["id"]

    got = auth_client.get(f"/api/servers/{server_id}")
    assert got.status_code == 200
    # PlexAdapter.supported_scan_modes is frozenset({targeted, library}); from_model sorts by value,
    # so "library" < "targeted". (This is the *supported* set, distinct from the server's default
    # scan_mode of "targeted".)
    assert got.json()["supported_scan_modes"] == ["library", "targeted"]


def test_create_auth_type_without_secret_is_422(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/servers", json={"name": "plex2", "type": "plex"})
    assert resp.status_code == 422


def test_create_webhook_without_secret_ok(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/servers", json={"name": "hook", "type": "webhook"})
    assert resp.status_code == 201
    assert resp.json()["has_secret"] is False


def test_patch_disables_server(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/servers", json={"name": "emby", "type": "emby", "secret": "t"}
    ).json()
    resp = auth_client.patch(f"/api/servers/{created['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_patch_clearing_secret_is_422(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/servers", json={"name": "emby2", "type": "emby", "secret": "t"}
    ).json()
    resp = auth_client.patch(f"/api/servers/{created['id']}", json={"secret": None})
    assert resp.status_code == 422


def test_get_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/servers/999").status_code == 404


def test_patch_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.patch("/api/servers/999", json={"enabled": False}).status_code == 404


def test_delete_server(auth_client: TestClient) -> None:
    created = auth_client.post("/api/servers", json={"name": "hook2", "type": "webhook"}).json()
    assert auth_client.delete(f"/api/servers/{created['id']}").status_code == 204
    assert auth_client.get(f"/api/servers/{created['id']}").status_code == 404


@respx.mock
def test_test_endpoint_reports_reachable(auth_client: TestClient, repo: Repo) -> None:
    # Emby's test() GETs {base}/System/Info with the token header (tests/servers/test_emby.py).
    server = repo.create_server(
        ServerCreate(
            name="emby-probe", type=ServerType.emby, base_url="http://emby:8096", secret="t"
        )
    )
    assert server.id is not None
    route = respx.get("http://emby:8096/System/Info").mock(return_value=httpx.Response(200))
    resp = auth_client.post(f"/api/servers/{server.id}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert route.called


@respx.mock
def test_test_endpoint_reports_failure(auth_client: TestClient, repo: Repo) -> None:
    server = repo.create_server(
        ServerCreate(name="emby-bad", type=ServerType.emby, base_url="http://emby:8096", secret="t")
    )
    assert server.id is not None
    respx.get("http://emby:8096/System/Info").mock(return_value=httpx.Response(401))
    resp = auth_client.post(f"/api/servers/{server.id}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_test_endpoint_missing_is_404(auth_client: TestClient) -> None:
    assert auth_client.post("/api/servers/999/test").status_code == 404
