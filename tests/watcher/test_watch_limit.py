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
    count = watch_limit.count_dirs(
        [str(tmp_path / "r1"), str(tmp_path / "r2")], frozenset()
    )

    assert count == 3
