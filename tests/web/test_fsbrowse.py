"""Unit tests for the folder-picker directory core (spec 2026-06-22)."""

import os

import pytest

from mediascanmonitor.web.fsbrowse import MAX_ENTRIES, list_directory


def test_lists_only_subdirectories(tmp_path):
    (tmp_path / "tv").mkdir()
    (tmp_path / "movies").mkdir()
    (tmp_path / "note.txt").write_text("x")
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == ["movies", "tv"]  # sorted, file excluded
    assert all(e.path == os.path.join(str(tmp_path), e.name) for e in listing.entries)


def test_entries_sorted_case_insensitively(tmp_path):
    for name in ("Zeta", "alpha", "Beta"):
        (tmp_path / name).mkdir()
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == ["alpha", "Beta", "Zeta"]


def test_parent_computed_and_none_at_root(tmp_path):
    (tmp_path / "child").mkdir()
    listing = list_directory(str(tmp_path / "child"))
    assert listing.parent == str(tmp_path)
    root = list_directory("/")
    assert root.parent is None
    assert root.path == "/"


def test_ignored_dirs_skipped_dotdirs_kept(tmp_path):
    for name in ("@eaDir", "#snapshot", ".hidden", "tv"):
        (tmp_path / name).mkdir()
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == [".hidden", "tv"]


def test_symlinked_dir_keeps_symlink_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    listing = list_directory(str(link))
    assert listing.path == str(link)  # NOT str(real) — symlinks are not resolved (decision 5)
    assert [e.name for e in listing.entries] == ["inside"]


def test_dotdot_normalized_lexically(tmp_path):
    (tmp_path / "real").mkdir()
    listing = list_directory(str(tmp_path / "real" / ".."))
    assert listing.path == str(tmp_path)


def test_truncated_when_over_cap(tmp_path):
    for i in range(MAX_ENTRIES + 5):
        (tmp_path / f"d{i:04d}").mkdir()
    listing = list_directory(str(tmp_path))
    assert listing.truncated is True
    assert len(listing.entries) == MAX_ENTRIES


def test_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_directory(str(tmp_path / "nope"))


def test_file_path_raises_not_a_directory(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        list_directory(str(f))


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_permission_denied_raises(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "child").mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            list_directory(str(locked))
    finally:
        locked.chmod(0o755)  # restore so pytest can clean up tmp_path
