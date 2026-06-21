"""/ui settings: gate toggle persists + rebuilds; recheck rebuilds. HTML status partial out."""

import httpx

from mediascanmonitor.engine import EngineState


def test_ui_settings_persists_gate_and_rebuilds(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    engine.state = EngineState.running
    before = engine.rebuild_calls
    resp = auth_client.post("/ui/settings", data={"inotify_gate": "off"})
    assert resp.status_code == 200
    assert "off" in resp.text  # status partial reflects the new gate
    assert repo.get_setting("inotify_gate") == "off"
    assert engine.rebuild_calls == before + 1


def test_ui_settings_rejects_bad_value(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    resp = auth_client.post("/ui/settings", data={"inotify_gate": "banana"})
    assert resp.status_code == 422  # validated against the 2-member literal


def test_ui_recheck_rebuilds(
    auth_client: httpx.Client,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    engine.state = EngineState.blocked
    before = engine.rebuild_calls
    resp = auth_client.post("/ui/recheck")
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1
