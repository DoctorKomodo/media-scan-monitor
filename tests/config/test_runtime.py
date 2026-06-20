"""Tests for config/runtime.py — runtime snapshot dataclasses + builder."""

import dataclasses

import pytest
from mediascanmonitor.config.runtime import FolderRoute, RuntimeConfig, ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType


def test_server_runtime_fields_frozen_slotted() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="https://plex.local:32400",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="token-abc",
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    assert sr.server_id == 1
    assert sr.secret == "token-abc"
    assert sr.type is ServerType.plex
    assert not hasattr(sr, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        sr.secret = "leak"  # type: ignore[misc]


def test_server_runtime_secret_excluded_from_repr() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="",
        verify_tls=True,
        timeout_seconds=10.0,
        secret="super-secret-token",
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    assert "super-secret-token" not in repr(sr)  # invariant 3: never in a repr
    assert sr.secret == "super-secret-token"      # still reachable by attribute


def test_folder_route_fields_frozen_slotted() -> None:
    fr = FolderRoute(
        server_id=1,
        server_name="plex-main",
        path="/data/media/tv",
        extensions=frozenset({"mkv", "srt"}),
        library_id="2",
        scan_mode=ScanMode.targeted,
    )
    assert fr.path == "/data/media/tv"
    assert fr.extensions == frozenset({"mkv", "srt"})
    assert not hasattr(fr, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fr.path = "/elsewhere"  # type: ignore[misc]


def test_runtime_config_fields_frozen_slotted() -> None:
    sr = ServerRuntime(
        server_id=1,
        name="plex-main",
        type=ServerType.plex,
        base_url="",
        verify_tls=True,
        timeout_seconds=10.0,
        secret=None,
        scan_mode=ScanMode.targeted,
        debounce_mode=DebounceMode.trailing,
        debounce_window_seconds=30,
        retry_attempts=3,
        webhook_method=None,
        webhook_headers_json=None,
        webhook_body_template=None,
    )
    fr = FolderRoute(
        server_id=1,
        server_name="plex-main",
        path="/data/media/tv",
        extensions=frozenset(),
        library_id="2",
        scan_mode=ScanMode.targeted,
    )
    cfg = RuntimeConfig(
        watch_paths=frozenset({"/data/media/tv"}),
        routes=(fr,),
        servers={1: sr},
        ignore_dirs=frozenset({"@eaDir"}),
    )
    assert cfg.watch_paths == frozenset({"/data/media/tv"})
    assert cfg.routes == (fr,)
    assert cfg.servers == {1: sr}
    assert cfg.ignore_dirs == frozenset({"@eaDir"})
    assert not hasattr(cfg, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.routes = ()  # type: ignore[misc]
