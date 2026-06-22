---
name: run-dev-server
description: Launch media-scan-monitor locally with sample data for manual testing or screenshots. Use when asked to run, start, serve, or screenshot the app, or to reach the dev UI over the LAN.
---

# Run the dev server

Stand up the full app (web dashboard + engine) on one event loop, seeded with sample
servers/folders, bound to all interfaces so it's reachable over the LAN.

## One command

```bash
scripts/dev_serve.sh
```

This runs `uv sync` → `scripts/dev_seed.py` → `msm run`. It prints the local and LAN URLs
and the password on startup. Default: `http://0.0.0.0:8099`, password `dev`. Ctrl-C to stop.

Override via env: `MSM_PORT=8080 MSM_PASSWORD=hunter2 scripts/dev_serve.sh`.

To run it without blocking the session, launch in the background and poll
`http://127.0.0.1:<port>/login` for a `200` before driving the UI.

## State

Lives in `./dev-data/` (gitignored, throwaway). Delete it to reset; `dev_seed.py` reseeds
the sample servers only when the DB has none, but always (re)sets the password.

## Logging in (e.g. with Playwright)

1. `GET /login`, fill `input[type=password]` with the password, submit. Lands on `/`.
2. Pages: `/` (dashboard), `/servers`, `/servers/{id}`, `/settings`, `/events`.

If you edit `static/app.css` while the browser is open, the old stylesheet is cached —
force a reload by swapping the `<link>` href to `/static/app.css?v=<ts>`.

## Useful facts (so you don't have to rediscover them)

- Entry point: `msm run` (or `msm run --no-web` for headless engine). Env config:
  `MSM_DB_PATH`, `MSM_SECRET_KEY_FILE`, `MSM_HOST`, `MSM_PORT`, `MSM_PASSWORD`.
- DB access: `Repo(session_factory(init_db(db_path)), SecretBox(load_or_create_key(key_path)))`
  from `mediascanmonitor.db.{session,repo,crypto}`. Password: `set_password(repo, ...)` from
  `mediascanmonitor.web.auth`.
- `repo.list_servers()` returns rows **without** `folders` loaded — use `repo.list_folders(id)`.
- Enums (`mediascanmonitor.db.models`): `ServerType.{plex,emby,jellyfin,audiobookshelf,webhook}`,
  `ScanMode.{targeted,library}`, `DebounceMode.{off,trailing}`.
