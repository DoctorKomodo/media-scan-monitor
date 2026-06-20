"""Tests for config/defaults.py — pure constants."""

from mediascanmonitor.config.defaults import (
    DEFAULT_DEBOUNCE_BY_TYPE,
    DEFAULT_DEBOUNCE_WINDOW_SECONDS,
    EXTENSION_PRESETS,
    IGNORE_DIRS,
)
from mediascanmonitor.db.models import DebounceMode, ServerType


def test_ignore_dirs_contains_synology_system_folders() -> None:
    assert IGNORE_DIRS == frozenset({"@eaDir", "#snapshot", "#recycle", "@tmp"})


def test_ignore_dirs_is_frozenset() -> None:
    assert isinstance(IGNORE_DIRS, frozenset)


def test_default_debounce_window_seconds() -> None:
    assert DEFAULT_DEBOUNCE_WINDOW_SECONDS == 30


def test_extension_presets_have_expected_keys() -> None:
    assert set(EXTENSION_PRESETS) == {"video", "subtitles", "audio"}


def test_extension_presets_are_normalized_tuples() -> None:
    for exts in EXTENSION_PRESETS.values():
        assert isinstance(exts, tuple)
        for ext in exts:
            # Already normalized: lowercase, no leading dot, no whitespace.
            assert ext == ext.strip().lstrip(".").lower()
    assert "mkv" in EXTENSION_PRESETS["video"]
    assert "srt" in EXTENSION_PRESETS["subtitles"]
    assert "mp3" in EXTENSION_PRESETS["audio"]


def test_default_debounce_by_type_covers_every_server_type() -> None:
    assert set(DEFAULT_DEBOUNCE_BY_TYPE) == set(ServerType)


def test_default_debounce_by_type_values() -> None:
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.webhook] == DebounceMode.off
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.plex] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.emby] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.jellyfin] == DebounceMode.trailing
    assert DEFAULT_DEBOUNCE_BY_TYPE[ServerType.audiobookshelf] == DebounceMode.trailing
