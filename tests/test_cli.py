"""Smoke tests for the CLI skeleton (Phase 0)."""

import pytest

from mediascanmonitor import __version__
from mediascanmonitor.cli import build_parser, main


def test_version_string_is_set() -> None:
    assert __version__
    assert __version__.count(".") >= 2


def test_no_command_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "media-scan-monitor" in out
    assert "run" in out


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_command_not_yet_implemented() -> None:
    # `run` is intentionally a stub until Phase 1; it must fail loudly, not silently.
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert "Phase 1" in str(exc.value)


def test_parser_exposes_no_web_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--no-web"])
    assert args.command == "run"
    assert args.no_web is True
