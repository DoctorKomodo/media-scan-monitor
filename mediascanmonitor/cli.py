"""Command-line entrypoint.

Phase 0 provides the argument-parsing skeleton only. The ``run`` command will
start the engine (and, unless ``--no-web`` is given, the web dashboard) once the
engine and web layers land in later phases.
"""

import argparse
from collections.abc import Sequence

from mediascanmonitor import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="media-scan-monitor",
        description=(
            "Watch media folders and fan out targeted scan/refresh events to "
            "Plex, Emby, Jellyfin, Audiobookshelf, and generic webhooks."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = subparsers.add_parser(
        "run",
        help="Run the watcher engine (and the web dashboard unless --no-web).",
    )
    run.add_argument(
        "--no-web",
        action="store_true",
        help="Run the engine headless, without serving the web dashboard.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "run":
        # Wired up in Phase 1 (engine) and Phase 3 (web).
        raise SystemExit("`run` is not implemented yet (arrives in Phase 1).")

    parser.error(f"unknown command: {args.command!r}")
    return 2  # unreachable; parser.error exits, but keeps mypy/control-flow honest


if __name__ == "__main__":
    raise SystemExit(main())
