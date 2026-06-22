"""Seed a local dev database with a password and a few sample servers/folders.

Idempotent: sets the app password (from ``MSM_PASSWORD``, default ``dev``) and, only when
the DB has no servers yet, inserts one of each backend type so the UI has something to show.
Reads the same ``MSM_DB_PATH`` / ``MSM_SECRET_KEY_FILE`` env vars the app uses, so point them
at a throwaway dev dir before running (``scripts/dev_serve.sh`` does this for you).

    MSM_DB_PATH=dev-data/app.db MSM_SECRET_KEY_FILE=dev-data/secret.key \
        MSM_PASSWORD=dev uv run python scripts/dev_seed.py
"""

import os
from pathlib import Path

from mediascanmonitor.db.crypto import SecretBox, load_or_create_key
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.db.repo import Repo
from mediascanmonitor.db.schemas import FolderCreate, ServerCreate
from mediascanmonitor.db.session import init_db, session_factory
from mediascanmonitor.web.auth import set_password

_SAMPLES: list[tuple[ServerCreate, list[FolderCreate]]] = [
    (
        ServerCreate(
            name="Plex — Living Room",
            type=ServerType.plex,
            base_url="http://10.0.0.20:32400",
            secret="demo-token",
            scan_mode=ScanMode.targeted,
            debounce_mode=DebounceMode.trailing,
            debounce_window_seconds=30,
            enabled=True,
        ),
        [
            FolderCreate(path="/data/tv", library_id="2", extensions=["mkv", "mp4"], enabled=True),
            FolderCreate(
                path="/data/movies", library_id="1", extensions=["mkv", "mp4", "avi"], enabled=True
            ),
            FolderCreate(path="/data/anime", library_id="5", extensions=["mkv"], enabled=False),
        ],
    ),
    (
        ServerCreate(
            name="Jellyfin — Basement",
            type=ServerType.jellyfin,
            base_url="http://10.0.0.31:8096",
            secret="demo-token",
            scan_mode=ScanMode.library,
            debounce_mode=DebounceMode.trailing,
            debounce_window_seconds=45,
            verify_tls=False,
            enabled=True,
        ),
        [FolderCreate(path="/data/music", library_id="audio", extensions=["flac", "mp3"])],
    ),
    (
        ServerCreate(
            name="Audiobookshelf",
            type=ServerType.audiobookshelf,
            base_url="https://abs.home.lan",
            secret="demo-token",
            scan_mode=ScanMode.library,
            debounce_mode=DebounceMode.trailing,
            debounce_window_seconds=60,
            enabled=False,
        ),
        [FolderCreate(path="/data/audiobooks", library_id="lib_main", extensions=["m4b", "mp3"])],
    ),
    (
        ServerCreate(
            name="Home Assistant webhook",
            type=ServerType.webhook,
            base_url="",
            secret=None,
            scan_mode=ScanMode.library,
            debounce_mode=DebounceMode.off,
            enabled=True,
            webhook_method="POST",
            webhook_headers_json='{"X-Token": "demo"}',
            webhook_body_template='{"path": "{{path}}"}',
        ),
        [FolderCreate(path="/data/tv", extensions=["mkv"])],
    ),
]


def main() -> None:
    db_path = Path(os.environ.get("MSM_DB_PATH", "dev-data/app.db"))
    key_path = Path(os.environ.get("MSM_SECRET_KEY_FILE", "dev-data/secret.key"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    repo = Repo(session_factory(init_db(db_path)), SecretBox(load_or_create_key(key_path)))

    set_password(repo, os.environ.get("MSM_PASSWORD", "dev"))

    if repo.list_servers():
        print(f"DB already has {len(repo.list_servers())} server(s); leaving them as-is.")
        return

    for server, folders in _SAMPLES:
        created = repo.create_server(server)
        assert created.id is not None
        for folder in folders:
            repo.create_folder(created.id, folder)
    print(f"Seeded {len(_SAMPLES)} sample servers into {db_path}.")


if __name__ == "__main__":
    main()
