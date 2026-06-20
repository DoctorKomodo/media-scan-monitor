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
