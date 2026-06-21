"""CLI tests: parser smoke (Phase 0) + headless run wiring (Phase 1, sub-plan 06)."""

import asyncio
from typing import cast

import pytest

from mediascanmonitor import __version__
from mediascanmonitor import cli as cli_module
from mediascanmonitor.cli import build_parser, main, serve_headless
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.watcher.watch_limit import WatchLimitStatus
from tests._helpers import (
    FakeClient,
    FakeWatcher,
    RecordingAdapter,
    make_config,
    make_route,
    make_server_runtime,
)

# --- Phase 0 parser smoke (unchanged) --------------------------------------


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


def test_parser_exposes_no_web_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--no-web"])
    assert args.command == "run"
    assert args.no_web is True


# --- Phase 1: `run` dispatch (revised + new) -------------------------------


def test_run_without_no_web_prints_phase3_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Revised from the Phase-0 stub: clear message to stderr, exit code 2, no traceback.
    code = main(["run"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Phase 3" in err
    assert "--no-web" in err


def test_run_no_web_invokes_serve_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(cli_module, "_build_repo", lambda: cast(Repo, object()))
    monkeypatch.setattr(cli_module, "configure_logging", lambda **_: None)

    async def fake_serve(repo: Repo) -> int:
        calls.append(True)
        return 0

    monkeypatch.setattr(cli_module, "serve_headless", fake_serve)

    assert main(["run", "--no-web"]) == 0
    assert calls == [True]


def test_run_no_web_reports_startup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> Repo:
        raise RuntimeError("no /config/secret.key")

    monkeypatch.setattr(cli_module, "_build_repo", boom)
    monkeypatch.setattr(cli_module, "configure_logging", lambda **_: None)

    code = main(["run", "--no-web"])
    assert code == 1
    assert "startup error" in capsys.readouterr().err


# --- serve_headless coroutine (testable assembly, no real signals) ---------


async def test_serve_headless_shuts_down_on_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module.engine_module,
        "build_runtime_config",
        lambda repo: make_config([], []),
    )
    monkeypatch.setattr(
        cli_module.engine_module,
        "create_adapter",
        lambda server, client: RecordingAdapter(server, client),
    )
    monkeypatch.setattr(cli_module.engine_module, "build_client", lambda **_: FakeClient())

    watcher = FakeWatcher()
    stop = asyncio.Event()
    stop.set()  # request shutdown immediately

    await serve_headless(
        cast(Repo, object()), watcher=watcher, stop_event=stop, install_signals=False
    )

    assert watcher.closed is True  # engine.aclose() ran -> clean shutdown


async def test_serve_headless_blocked_returns_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    server = make_server_runtime(1, name="plex")
    route = make_route(1, name="plex", path="/data/tv", library_id="2")
    monkeypatch.setattr(
        cli_module.engine_module,
        "build_runtime_config",
        lambda repo: make_config([route], [server]),
    )
    monkeypatch.setattr(
        cli_module.engine_module,
        "create_adapter",
        lambda server, client: RecordingAdapter(server, client),
    )
    monkeypatch.setattr(cli_module.engine_module, "build_client", lambda **_: FakeClient())
    monkeypatch.setattr(
        cli_module.engine_module,
        "check_watch_limit",
        lambda paths, ignore: WatchLimitStatus(
            current=10, dirs=100, needed=120, recommended=144, ok=False
        ),
    )

    class _StubRepo:
        def get_setting(self, key: str) -> str | None:
            return "enforce"

    watcher = FakeWatcher()
    code = await serve_headless(cast(Repo, _StubRepo()), watcher=watcher, install_signals=False)

    assert code == 3
    assert watcher.roots_history == []  # blocked before set_roots (headless contract)
