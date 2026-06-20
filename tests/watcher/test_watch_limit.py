"""Unit tests for the inotify watch-limit gate (runs on every platform)."""

from pathlib import Path

import pytest

from mediascanmonitor.watcher import watch_limit


def test_read_max_user_watches_parses_the_proc_file(tmp_path: Path) -> None:
    proc = tmp_path / "max_user_watches"
    proc.write_text("131072\n")

    assert watch_limit.read_max_user_watches(str(proc)) == 131072


def test_read_max_user_watches_strips_surrounding_whitespace(tmp_path: Path) -> None:
    proc = tmp_path / "max_user_watches"
    proc.write_text("  8192  \n")

    assert watch_limit.read_max_user_watches(str(proc)) == 8192


def test_count_dirs_counts_root_and_subdirs_skipping_ignored(tmp_path: Path) -> None:
    # tmp_path
    #   show_a/
    #     season_1/
    #   show_b/
    #   @eaDir/            <- ignored (and its children must not be counted)
    #     thumbs/
    (tmp_path / "show_a" / "season_1").mkdir(parents=True)
    (tmp_path / "show_b").mkdir()
    (tmp_path / "@eaDir" / "thumbs").mkdir(parents=True)

    # Counted dirs: tmp_path, show_a, show_a/season_1, show_b  -> 4
    count = watch_limit.count_dirs([str(tmp_path)], frozenset({"@eaDir"}))

    assert count == 4


def test_count_dirs_skips_missing_roots(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"

    assert watch_limit.count_dirs([str(missing)], frozenset()) == 0


def test_count_dirs_sums_multiple_roots(tmp_path: Path) -> None:
    (tmp_path / "r1" / "sub").mkdir(parents=True)
    (tmp_path / "r2").mkdir()

    # r1 + r1/sub = 2 ; r2 = 1 ; total 3
    count = watch_limit.count_dirs([str(tmp_path / "r1"), str(tmp_path / "r2")], frozenset())

    assert count == 3


def test_check_watch_limit_ok_with_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one dir to watch (the root); default headroom 1.2 -> needed = ceil(1*1.2) = 2
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 100)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset())

    assert status.current == 100
    assert status.dirs == 1  # raw dir count
    assert status.needed == 2  # ceil(1 * 1.2)
    assert status.recommended == 3  # ceil(2 * 1.2)
    assert status.ok is True


def test_check_watch_limit_not_ok_when_below_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build 100 directories under the root: raw dir count = 101 (root + 100).
    for i in range(100):
        (tmp_path / f"d{i}").mkdir()
    # headroom 1.2 -> needed = ceil(101*1.2) = ceil(121.2) = 122 ; 121 is below it.
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 121)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset())

    assert status.dirs == 101
    assert status.needed == 122
    assert status.ok is False  # 121 < 122


def test_check_watch_limit_ok_exactly_at_headroom_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # raw dir count = 1 (root only) ; headroom 2.0 -> needed = ceil(2.0) = 2 ; limit 2 is OK.
    monkeypatch.setattr(watch_limit, "read_max_user_watches", lambda: 2)

    status = watch_limit.check_watch_limit([str(tmp_path)], frozenset(), headroom=2.0)

    assert status.dirs == 1
    assert status.needed == 2
    assert status.ok is True  # 2 >= 2
