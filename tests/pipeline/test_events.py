"""Tests for pipeline/events.py — frozen slotted domain types."""

import dataclasses

import pytest

from mediascanmonitor.db.models import ScanMode
from mediascanmonitor.pipeline.events import FsEvent, FsEventType, ScanRequest


def test_fs_event_type_values() -> None:
    assert FsEventType.created.value == "created"
    assert FsEventType.moved_to.value == "moved_to"
    assert FsEventType.deleted.value == "deleted"
    assert FsEventType.moved_from.value == "moved_from"
    assert set(FsEventType) == {
        FsEventType.created,
        FsEventType.moved_to,
        FsEventType.deleted,
        FsEventType.moved_from,
    }


def test_fs_event_type_is_str_enum() -> None:
    # str-Enum so it serializes/compares as its value.
    assert FsEventType.created == "created"


def test_fs_event_fields() -> None:
    ev = FsEvent(path="/data/media/tv/Show/ep.mkv", event_type=FsEventType.created, is_dir=False)
    assert ev.path == "/data/media/tv/Show/ep.mkv"
    assert ev.event_type is FsEventType.created
    assert ev.is_dir is False


def test_fs_event_is_frozen() -> None:
    ev = FsEvent(path="/x", event_type=FsEventType.deleted, is_dir=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.path = "/y"  # type: ignore[misc]


def test_fs_event_is_slotted() -> None:
    ev = FsEvent(path="/x", event_type=FsEventType.deleted, is_dir=True)
    assert not hasattr(ev, "__dict__")


def test_scan_request_fields() -> None:
    req = ScanRequest(
        server_id=1,
        server_name="plex-main",
        scan_mode=ScanMode.targeted,
        scan_path="/data/media/tv/Shoresy",
        library_id="2",
        scan_key="/data/media/tv/Shoresy",
        event_type=FsEventType.created,
        file_path="/data/media/tv/Shoresy/S01E01.mkv",
        top_folder="Shoresy",
    )
    assert req.server_id == 1
    assert req.server_name == "plex-main"
    assert req.scan_mode is ScanMode.targeted
    assert req.scan_path == "/data/media/tv/Shoresy"
    assert req.library_id == "2"
    assert req.scan_key == "/data/media/tv/Shoresy"
    assert req.event_type is FsEventType.created
    assert req.file_path == "/data/media/tv/Shoresy/S01E01.mkv"
    assert req.top_folder == "Shoresy"


def test_scan_request_library_mode_allows_none_scan_path() -> None:
    req = ScanRequest(
        server_id=3,
        server_name="emby",
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="movies",
        scan_key="lib:movies",
        event_type=FsEventType.moved_to,
        file_path="/data/media/movies/Dune/Dune.mkv",
        top_folder=None,
    )
    assert req.scan_path is None
    assert req.top_folder is None
    assert req.scan_key == "lib:movies"


def test_scan_request_is_frozen_and_slotted() -> None:
    req = ScanRequest(
        server_id=1,
        server_name="x",
        scan_mode=ScanMode.targeted,
        scan_path="/a",
        library_id=None,
        scan_key="/a",
        event_type=FsEventType.deleted,
        file_path="/a/b.mkv",
        top_folder=None,
    )
    assert not hasattr(req, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.server_id = 99  # type: ignore[misc]
