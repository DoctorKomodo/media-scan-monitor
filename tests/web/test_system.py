"""System & status routes (contract §H): liveness, readiness, status, gate control."""

from fastapi.testclient import TestClient

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import ServerCreate
from mediascanmonitor.engine import EngineState
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus


def test_health_is_unauthenticated_and_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_requires_auth(client: TestClient) -> None:
    assert client.get("/ready").status_code == 401


def test_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 401


def test_put_inotify_gate_requires_auth(client: TestClient) -> None:
    resp = client.put("/api/settings/inotify-gate", json={"inotify_gate": "off"})
    assert resp.status_code == 401


def test_recheck_requires_auth(client: TestClient) -> None:
    assert client.post("/api/engine/recheck").status_code == 401


def test_ready_200_when_running(auth_client: TestClient, engine: object) -> None:
    engine.state = EngineState.running  # type: ignore[attr-defined]
    assert auth_client.get("/ready").status_code == 200


def test_ready_503_when_blocked(auth_client: TestClient, engine: object) -> None:
    engine.state = EngineState.blocked  # type: ignore[attr-defined]
    resp = auth_client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "blocked"


def test_status_reports_state_counts_and_watch_limit(
    auth_client: TestClient, repo: Repo, engine: object
) -> None:
    repo.create_server(ServerCreate(name="a", type=ServerType.webhook, base_url="https://h/x"))
    repo.create_server(
        ServerCreate(name="b", type=ServerType.webhook, base_url="https://h/y", enabled=False)
    )
    engine.state = EngineState.running  # type: ignore[attr-defined]
    engine.watch_limit = WatchLimitStatus(  # type: ignore[attr-defined]
        current=100, dirs=40, needed=48, recommended=58, ok=True
    )

    body = auth_client.get("/api/status").json()
    assert body["engine_state"] == "running"
    assert body["inotify_gate"] == "enforce"  # default
    assert body["server_count"] == 2
    assert body["enabled_server_count"] == 1
    assert body["watch_current"] == 100
    assert body["watch_needed"] == 48
    assert body["watch_ok"] is True


def test_status_watch_fields_none_when_unevaluated(auth_client: TestClient, engine: object) -> None:
    engine.watch_limit = None  # type: ignore[attr-defined]
    body = auth_client.get("/api/status").json()
    assert body["watch_current"] is None
    assert body["watch_ok"] is None


def test_put_inotify_gate_sets_setting_and_rebuilds(
    auth_client: TestClient, repo: Repo, engine: object
) -> None:
    resp = auth_client.put("/api/settings/inotify-gate", json={"inotify_gate": "off"})
    assert resp.status_code == 200
    assert resp.json()["inotify_gate"] == "off"
    assert repo.get_setting("inotify_gate") == "off"
    assert engine.rebuild_calls >= 1  # type: ignore[attr-defined]


def test_put_inotify_gate_rejects_unknown_value(auth_client: TestClient) -> None:
    assert (
        auth_client.put("/api/settings/inotify-gate", json={"inotify_gate": "maybe"}).status_code
        == 422
    )


def test_recheck_rebuilds_and_returns_status(auth_client: TestClient, engine: object) -> None:
    before = engine.rebuild_calls  # type: ignore[attr-defined]
    resp = auth_client.post("/api/engine/recheck")
    assert resp.status_code == 200
    assert "engine_state" in resp.json()
    assert engine.rebuild_calls == before + 1  # type: ignore[attr-defined]
