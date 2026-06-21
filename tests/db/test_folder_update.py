"""FolderUpdate validators + repo.get_folder / repo.update_folder (contract §E)."""

import pytest

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate


def _make_folder(repo: Repo) -> int:
    server = repo.create_server(ServerCreate(name="plex", type=ServerType.plex, secret="t"))
    assert server.id is not None
    folder = repo.create_folder(
        server.id,
        FolderCreate(path="/data/tv", library_id="2", extensions=["MKV", ".mp4"]),
    )
    assert folder.id is not None
    return folder.id


def test_folder_update_normalizes_path() -> None:
    data = FolderUpdate(path="/data/tv/../movies/")
    assert data.path == "/data/movies"


def test_folder_update_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        FolderUpdate(path="relative/dir")


def test_folder_update_normalizes_and_dedupes_extensions() -> None:
    data = FolderUpdate(extensions=[".MKV", "mkv", "", " mp4 "])
    assert data.extensions == ["mkv", "mp4"]


def test_folder_update_unset_fields_excluded() -> None:
    dumped = FolderUpdate(enabled=False).model_dump(exclude_unset=True)
    assert dumped == {"enabled": False}


def test_get_folder_returns_none_for_missing(repo: Repo) -> None:
    assert repo.get_folder(999) is None


def test_get_folder_loads_filetypes(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    folder = repo.get_folder(folder_id)
    assert folder is not None
    assert sorted(ft.extension for ft in folder.filetypes) == ["mkv", "mp4"]


def test_update_folder_missing_raises_keyerror(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.update_folder(999, FolderUpdate(enabled=False))


def test_update_folder_partial_leaves_other_fields(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(enabled=False))
    assert updated.enabled is False
    assert updated.path == "/data/tv"
    assert updated.library_id == "2"
    assert sorted(ft.extension for ft in updated.filetypes) == ["mkv", "mp4"]


def test_update_folder_replaces_extensions(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(extensions=[".AVI", "avi", "flac"]))
    assert sorted(ft.extension for ft in updated.filetypes) == ["avi", "flac"]


def test_update_folder_empty_extensions_clears(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(extensions=[]))
    assert list(updated.filetypes) == []


def test_update_folder_omitted_extensions_unchanged(repo: Repo) -> None:
    folder_id = _make_folder(repo)
    updated = repo.update_folder(folder_id, FolderUpdate(library_id="9"))
    assert updated.library_id == "9"
    assert sorted(ft.extension for ft in updated.filetypes) == ["mkv", "mp4"]
