"""Tests for the Repo CRUD/crypto contract (contract section 4)."""

from collections.abc import Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from mediascanmonitor.db.models import FileType, Folder, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate, ServerUpdate


def make_server(
    name: str = "plex1", *, enabled: bool = True, secret: str | None = "tok"
) -> ServerCreate:
    return ServerCreate(
        name=name,
        type=ServerType.plex,
        base_url="https://plex:32400",
        secret=secret,
        enabled=enabled,
    )


def test_create_server_encrypts_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret="my-token"))
    assert server.id is not None
    assert server.secret_encrypted is not None
    assert server.secret_encrypted != "my-token"
    assert repo.resolve_secret(server) == "my-token"


def test_create_server_without_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret=None))
    assert server.secret_encrypted is None
    assert repo.resolve_secret(server) is None


def test_create_server_with_folders_persists_both(repo: Repo) -> None:
    server = repo.create_server_with_folders(
        make_server(name="combined"),
        [
            FolderCreate(path="/data/tv", library_id="2", extensions=["mkv", "MP4", "mkv"]),
            FolderCreate(path="/data/movies", extensions=[]),
        ],
    )
    assert server.id is not None
    folders = repo.list_folders(server.id)
    assert {f.path for f in folders} == {"/data/tv", "/data/movies"}
    tv = next(f for f in folders if f.path == "/data/tv")
    assert sorted(ft.extension for ft in tv.filetypes) == ["mkv", "mp4"]  # normalized + deduped


def test_update_server_with_folders_changes_fields_and_swaps_folders(repo: Repo) -> None:
    server = repo.create_server_with_folders(
        make_server(name="combo"), [FolderCreate(path="/old", extensions=["avi"])]
    )
    assert server.id is not None
    updated = repo.update_server_with_folders(
        server.id,
        ServerUpdate(enabled=False),
        [
            FolderCreate(path="/data/tv", extensions=["mkv", "MP4"]),
            FolderCreate(path="/data/movies", extensions=["mkv"]),
        ],
    )
    assert updated.enabled is False
    folders = repo.list_folders(server.id)
    assert {f.path for f in folders} == {"/data/tv", "/data/movies"}  # /old replaced wholesale
    tv = next(f for f in folders if f.path == "/data/tv")
    assert sorted(ft.extension for ft in tv.filetypes) == ["mkv", "mp4"]  # normalized + deduped


def test_update_server_with_folders_empty_clears_all(repo: Repo) -> None:
    server = repo.create_server_with_folders(
        make_server(name="clearfolders"), [FolderCreate(path="/x", extensions=["mkv"])]
    )
    assert server.id is not None
    repo.update_server_with_folders(server.id, ServerUpdate(), [])
    assert repo.list_folders(server.id) == []


def test_update_server_with_folders_unknown_server_raises(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.update_server_with_folders(
            9999, ServerUpdate(), [FolderCreate(path="/data/tv", extensions=["mkv"])]
        )


def test_create_server_with_folders_is_atomic_on_duplicate_name(repo: Repo) -> None:
    existing = repo.create_server(make_server(name="dupe"))
    assert existing.id is not None
    with pytest.raises(IntegrityError):
        repo.create_server_with_folders(
            make_server(name="dupe"), [FolderCreate(path="/data/tv", extensions=["mkv"])]
        )
    # The whole transaction rolled back: no second server was added, and the folder that would
    # have been created went with it (a committed orphan is impossible — folders FK to a server).
    assert len(repo.list_servers()) == 1
    assert repo.list_folders(existing.id) == []


def test_get_server_round_trip_and_missing(repo: Repo) -> None:
    created = repo.create_server(make_server())
    assert created.id is not None
    fetched = repo.get_server(created.id)
    assert fetched is not None
    assert fetched.name == "plex1"
    assert repo.get_server(9999) is None


def test_list_servers_enabled_only(repo: Repo) -> None:
    repo.create_server(make_server(name="on", enabled=True))
    repo.create_server(make_server(name="off", enabled=False))
    assert len(repo.list_servers()) == 2
    enabled = repo.list_servers(enabled_only=True)
    assert [s.name for s in enabled] == ["on"]


def test_update_server_changes_fields_and_keeps_secret(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    updated = repo.update_server(
        server.id, ServerUpdate(base_url="https://new:32400", enabled=False)
    )
    assert updated.base_url == "https://new:32400"
    assert updated.enabled is False
    assert repo.resolve_secret(updated) == "tok"  # secret untouched


def test_update_server_reencrypts_secret(repo: Repo) -> None:
    server = repo.create_server(make_server(secret="old"))
    assert server.id is not None
    old_ciphertext = server.secret_encrypted
    updated = repo.update_server(server.id, ServerUpdate(secret="new"))
    assert updated.secret_encrypted != old_ciphertext
    assert repo.resolve_secret(updated) == "new"


def test_update_server_clears_secret_when_explicitly_none(repo: Repo) -> None:
    # explicit secret=None clears the stored credential (distinct from omitting it)
    server = repo.create_server(make_server(secret="tok"))
    assert server.id is not None
    assert server.secret_encrypted is not None
    updated = repo.update_server(server.id, ServerUpdate(secret=None))
    assert updated.secret_encrypted is None
    assert repo.resolve_secret(updated) is None


def test_delete_server_cascades_to_folders_and_filetypes(
    repo: Repo, factory: Callable[[], Session]
) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(
        server.id,
        FolderCreate(path="/data/tv", library_id="2", extensions=["mkv", "srt"]),
    )
    repo.delete_server(server.id)
    assert repo.get_server(server.id) is None
    assert repo.list_folders(server.id) == []
    with factory() as session:
        assert list(session.exec(select(Folder)).all()) == []
        assert list(session.exec(select(FileType)).all()) == []


def test_create_folder_normalizes_path_and_extensions(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(
        server.id, FolderCreate(path="/data/tv/", extensions=[".MKV", " Srt "])
    )
    assert folder.path == "/data/tv"
    assert {ft.extension for ft in folder.filetypes} == {"mkv", "srt"}


def test_list_folders_returns_filetypes(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    repo.create_folder(server.id, FolderCreate(path="/data/tv", extensions=["mkv"]))
    folders = repo.list_folders(server.id)
    assert len(folders) == 1
    assert {ft.extension for ft in folders[0].filetypes} == {"mkv"}


def test_delete_folder(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(server.id, FolderCreate(path="/data/tv"))
    assert folder.id is not None
    repo.delete_folder(folder.id)
    assert repo.list_folders(server.id) == []


def test_set_filetypes_replaces_wholesale_and_normalizes(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(server.id, FolderCreate(path="/data/tv", extensions=["mkv", "mp4"]))
    assert folder.id is not None
    result = repo.set_filetypes(folder.id, [".SRT"])
    assert [ft.extension for ft in result] == ["srt"]
    folders = repo.list_folders(server.id)
    assert {ft.extension for ft in folders[0].filetypes} == {"srt"}


def test_set_filetypes_empty_list_means_all(repo: Repo) -> None:
    server = repo.create_server(make_server())
    assert server.id is not None
    folder = repo.create_folder(server.id, FolderCreate(path="/data/tv", extensions=["mkv"]))
    assert folder.id is not None
    result = repo.set_filetypes(folder.id, [])
    assert result == []
    folders = repo.list_folders(server.id)
    assert folders[0].filetypes == []


def test_settings_get_and_set(repo: Repo) -> None:
    assert repo.get_setting("missing") is None
    repo.set_setting("password_hash", "abc")
    assert repo.get_setting("password_hash") == "abc"
    repo.set_setting("password_hash", "def")  # overwrite
    assert repo.get_setting("password_hash") == "def"


def test_create_folder_unknown_server_raises(repo: Repo) -> None:
    # FK enforcement (PRAGMA foreign_keys=ON): a dangling server_id is rejected, not orphaned
    with pytest.raises(IntegrityError):
        repo.create_folder(9999, FolderCreate(path="/data/tv"))


def test_update_server_unknown_raises(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.update_server(9999, ServerUpdate(enabled=False))


def test_set_filetypes_unknown_folder_raises(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.set_filetypes(9999, ["mkv"])


def test_delete_missing_is_idempotent(repo: Repo) -> None:
    repo.delete_server(9999)  # no raise
    repo.delete_folder(9999)  # no raise
