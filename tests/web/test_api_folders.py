"""/api/servers/{id}/folders CRUD (contract §E)."""

import itertools

from starlette.testclient import TestClient

_counter = itertools.count(1)


def _make_server(auth_client: TestClient) -> int:
    resp = auth_client.post(
        "/api/servers", json={"name": f"hook-{next(_counter)}", "type": "webhook"}
    )
    assert resp.status_code == 201
    return int(resp.json()["id"])


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/servers/1/folders").status_code == 401


def test_create_and_list_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    resp = auth_client.post(
        f"/api/servers/{server_id}/folders",
        json={"path": "/data/tv", "library_id": "2", "extensions": ["MKV", "mp4", "mkv"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["path"] == "/data/tv"
    assert body["extensions"] == ["mkv", "mp4"]  # normalized + sorted + deduped

    listed = auth_client.get(f"/api/servers/{server_id}/folders")
    assert listed.status_code == 200
    assert [f["id"] for f in listed.json()] == [body["id"]]


def test_get_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders", json={"path": "/data/movies"}
    ).json()
    got = auth_client.get(f"/api/servers/{server_id}/folders/{folder['id']}")
    assert got.status_code == 200
    assert got.json()["path"] == "/data/movies"


def test_patch_folder_replaces_extensions(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(
        f"/api/servers/{server_id}/folders",
        json={"path": "/data/tv", "extensions": ["mkv"]},
    ).json()
    resp = auth_client.patch(
        f"/api/servers/{server_id}/folders/{folder['id']}",
        json={"enabled": False, "extensions": ["avi", "flac"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["extensions"] == ["avi", "flac"]


def test_patch_rejects_relative_path(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(f"/api/servers/{server_id}/folders", json={"path": "/data/tv"}).json()
    resp = auth_client.patch(
        f"/api/servers/{server_id}/folders/{folder['id']}", json={"path": "relative"}
    )
    assert resp.status_code == 422


def test_delete_folder(auth_client: TestClient) -> None:
    server_id = _make_server(auth_client)
    folder = auth_client.post(f"/api/servers/{server_id}/folders", json={"path": "/data/tv"}).json()
    assert auth_client.delete(f"/api/servers/{server_id}/folders/{folder['id']}").status_code == 204
    assert auth_client.get(f"/api/servers/{server_id}/folders/{folder['id']}").status_code == 404


def test_list_for_missing_server_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/servers/999/folders").status_code == 404


def test_get_folder_wrong_server_is_404(auth_client: TestClient) -> None:
    server_a = _make_server(auth_client)
    server_b = _make_server(auth_client)
    folder = auth_client.post(f"/api/servers/{server_a}/folders", json={"path": "/data/tv"}).json()
    assert auth_client.get(f"/api/servers/{server_b}/folders/{folder['id']}").status_code == 404


def test_patch_folder_wrong_server_is_404(auth_client: TestClient) -> None:
    server_a = _make_server(auth_client)
    server_b = _make_server(auth_client)
    folder = auth_client.post(f"/api/servers/{server_a}/folders", json={"path": "/data/tv"}).json()
    resp = auth_client.patch(
        f"/api/servers/{server_b}/folders/{folder['id']}", json={"enabled": False}
    )
    assert resp.status_code == 404


def test_delete_folder_wrong_server_is_404(auth_client: TestClient) -> None:
    server_a = _make_server(auth_client)
    server_b = _make_server(auth_client)
    folder = auth_client.post(f"/api/servers/{server_a}/folders", json={"path": "/data/tv"}).json()
    assert auth_client.delete(f"/api/servers/{server_b}/folders/{folder['id']}").status_code == 404
