"""/api/events/recent (contract §D / §G)."""

from starlette.testclient import TestClient

from mediascanmonitor.observ.events_bus import EventRecord, EventsBus


def _record(server_id: int, *, ok: bool = True) -> EventRecord:
    return EventRecord(
        ts="2026-06-20T18:30:00+00:00",
        server_id=server_id,
        server_name=f"srv{server_id}",
        scan_mode="library",
        scan_key="lib:5",
        scan_path=None,
        library_id="5",
        event_type="created",
        file_path="/data/media/x.mkv",
        ok=ok,
        status_code=200 if ok else 500,
        detail="ok" if ok else "boom",
    )


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/events/recent").status_code == 401


def test_recent_empty(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/events/recent")
    assert resp.status_code == 200
    assert resp.json() == []


def test_recent_returns_published_records(auth_client: TestClient, events_bus: EventsBus) -> None:
    events_bus.publish(_record(1))
    events_bus.publish(_record(2, ok=False))
    resp = auth_client.get("/api/events/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["server_id"] for r in body] == [1, 2]  # newest-last
    assert body[1]["ok"] is False
    # redaction sanity: no secret-shaped field present.
    assert all("secret" not in r for r in body)


def test_recent_respects_limit(auth_client: TestClient, events_bus: EventsBus) -> None:
    for i in range(5):
        events_bus.publish(_record(i))
    resp = auth_client.get("/api/events/recent?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_recent_rejects_bad_limit(auth_client: TestClient) -> None:
    assert auth_client.get("/api/events/recent?limit=0").status_code == 422
