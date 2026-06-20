"""Unit tests for the pure inotify-mask helpers (runs on every platform).

These import `inotify_backend` but never construct `InotifyBackend`, so they do
not touch the Linux-only asyncinotify C bindings.
"""

from mediascanmonitor.pipeline.events import FsEventType
from mediascanmonitor.watcher import inotify_backend as ib


def test_create_maps_to_created() -> None:
    assert ib.mask_to_event_type(ib.IN_CREATE) is FsEventType.created


def test_moved_to_maps_to_moved_to() -> None:
    assert ib.mask_to_event_type(ib.IN_MOVED_TO) is FsEventType.moved_to


def test_delete_maps_to_deleted() -> None:
    assert ib.mask_to_event_type(ib.IN_DELETE) is FsEventType.deleted


def test_moved_from_maps_to_moved_from() -> None:
    assert ib.mask_to_event_type(ib.IN_MOVED_FROM) is FsEventType.moved_from


def test_irrelevant_mask_maps_to_none() -> None:
    # IN_IGNORED (0x8000) and IN_ISDIR alone carry no create/move/delete bit.
    assert ib.mask_to_event_type(0x8000) is None
    assert ib.mask_to_event_type(ib.IN_ISDIR) is None
    # IN_Q_OVERFLOW is not a file event — events() handles it separately (resync).
    assert ib.mask_to_event_type(ib.IN_Q_OVERFLOW) is None


def test_create_with_isdir_still_maps_to_created() -> None:
    assert ib.mask_to_event_type(ib.IN_CREATE | ib.IN_ISDIR) is FsEventType.created


def test_mask_is_dir_detects_isdir_bit() -> None:
    assert ib.mask_is_dir(ib.IN_CREATE | ib.IN_ISDIR) is True
    assert ib.mask_is_dir(ib.IN_CREATE) is False
