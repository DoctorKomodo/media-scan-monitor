"""Command-line entrypoint.

``run`` serves the web dashboard + engine on one event loop (Phase 3).
``run --no-web`` runs the headless engine without the web layer.
"""

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from mediascanmonitor import __version__
from mediascanmonitor import engine as engine_module
from mediascanmonitor.db.crypto import SecretBox, load_or_create_key
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.session import init_db, session_factory
from mediascanmonitor.engine import Engine, EngineState
from mediascanmonitor.observ.logging import configure_logging
from mediascanmonitor.watcher.base import WatcherBackend
from mediascanmonitor.web.server import serve_web

__all__ = ["build_parser", "engine_module", "main", "serve_headless", "serve_web"]

_DEFAULT_DB_PATH = "/config/app.db"
_DEFAULT_KEY_PATH = "/config/secret.key"


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="media-scan-monitor",
        description=(
            "Watch media folders and fan out targeted scan/refresh events to "
            "Plex, Emby, Jellyfin, Audiobookshelf, and generic webhooks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    run = subparsers.add_parser(
        "run", help="Run the watcher engine (and the web dashboard unless --no-web)."
    )
    run.add_argument(
        "--no-web",
        action="store_true",
        help="Run the engine headless, without serving the web dashboard.",
    )
    return parser


def _load_key() -> bytes:
    """Resolve the Fernet secret key (env value > file > generate).

    Returns the urlsafe-base64 key bytes.
    """
    key_path = Path(os.environ.get("MSM_SECRET_KEY_FILE", _DEFAULT_KEY_PATH))
    env_key = os.environ.get("MSM_SECRET_KEY")
    return load_or_create_key(key_path, env_key=env_key)


def _build_repo() -> Repo:
    """Assemble the repository from env/Docker config. Raises on misconfiguration."""
    db_path = Path(os.environ.get("MSM_DB_PATH", _DEFAULT_DB_PATH))
    box = SecretBox(_load_key())
    engine = init_db(db_path)  # returns the Engine (contract §4); not a factory
    return Repo(session_factory(engine), box)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # e.g. non-main thread / Windows
            loop.add_signal_handler(sig, stop.set)


async def serve_headless(
    repo: Repo,
    *,
    watcher: WatcherBackend | None = None,
    stop_event: asyncio.Event | None = None,
    install_signals: bool = True,
) -> int:
    """Run the engine until SIGINT/SIGTERM (or ``stop_event``), then shut down.

    Returns a process exit code: ``0`` on clean shutdown, ``3`` if the inotify gate
    blocked startup (contract §10 — Bash-style block/exit, since headless has no UI to
    recover through). Designed for testability: inject ``watcher`` and ``stop_event`` and
    set ``install_signals=False`` to drive the lifecycle without real signals.
    """
    engine = Engine(repo, watcher=watcher)
    stop = stop_event if stop_event is not None else asyncio.Event()

    if install_signals:
        _install_signal_handlers(asyncio.get_running_loop(), stop)

    start_task = asyncio.create_task(engine.start(park_when_blocked=False))
    stop_task = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({start_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        return 3 if engine.state is EngineState.blocked else 0
    finally:
        await engine.aclose()  # closes the watcher -> events() ends -> start_task returns
        with contextlib.suppress(asyncio.CancelledError):
            await start_task
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        repo = _build_repo()
        session_secret = "" if args.no_web else _load_key().decode("ascii")
    except Exception as exc:  # fail fast with a clear message, not a traceback
        print(f"startup error: {exc}", file=sys.stderr)
        return 1

    configure_logging()
    if args.no_web:
        return asyncio.run(serve_headless(repo))  # 0 clean, 3 if the inotify gate blocked startup

    host = os.environ.get("MSM_HOST", "0.0.0.0")
    port = int(os.environ.get("MSM_PORT", "8080"))
    return asyncio.run(serve_web(repo, host=host, port=port, session_secret=session_secret))


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "run":
        return _cmd_run(args)

    parser.error(f"unknown command: {args.command!r}")
    return 2  # unreachable; parser.error exits, but keeps mypy/control-flow honest


if __name__ == "__main__":
    raise SystemExit(main())
