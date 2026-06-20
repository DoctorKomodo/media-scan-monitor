"""Tests for the Pydantic boundary schemas (contract section 4)."""

import pytest
from pydantic import ValidationError

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate


def test_server_create_defaults() -> None:
    s = ServerCreate(name="plex1", type=ServerType.plex)
    assert s.base_url == ""
    assert s.verify_tls is True
    assert s.timeout_seconds == 10.0
    assert s.secret is None
    assert s.scan_mode is ScanMode.targeted
    assert s.debounce_mode is DebounceMode.trailing
    assert s.debounce_window_seconds == 30
    assert s.retry_attempts == 3
    assert s.enabled is True


def test_server_create_accepts_plaintext_secret() -> None:
    s = ServerCreate(name="plex1", type=ServerType.plex, secret="plain")
    assert s.secret == "plain"


def test_server_update_tracks_only_set_fields() -> None:
    u = ServerUpdate(enabled=False)
    assert u.model_dump(exclude_unset=True) == {"enabled": False}

    u2 = ServerUpdate(secret="new", base_url="https://new:32400")
    assert u2.model_dump(exclude_unset=True) == {
        "secret": "new",
        "base_url": "https://new:32400",
    }

    assert ServerUpdate().model_dump(exclude_unset=True) == {}

    # explicit secret=None is distinct from omitting it: it clears the stored credential
    cleared = ServerUpdate(secret=None)
    assert cleared.model_dump(exclude_unset=True) == {"secret": None}


def test_folder_create_defaults() -> None:
    f = FolderCreate(path="/data/tv")
    assert f.path == "/data/tv"
    assert f.library_id is None
    assert f.extensions == []
    assert f.enabled is True


def test_folder_create_normalizes_path_and_extensions() -> None:
    f = FolderCreate(path="/data/tv/", extensions=[".MKV", "mkv", " Srt "])
    assert f.path == "/data/tv"  # trailing slash collapsed by the validator
    assert f.extensions == ["mkv", "srt"]  # normalized, deduped, order preserved


def test_folder_create_rejects_relative_path() -> None:
    with pytest.raises(ValidationError):
        FolderCreate(path="relative/tv")  # absoluteness enforced at the boundary
