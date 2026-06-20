# media-scan-monitor

Watch local media directories and fire **targeted scan/refresh events** at your media
servers when files are added, moved, or deleted. A single [inotify](https://man7.org/linux/man-pages/man7/inotify.7.html)
watcher fans out notifications to any number of configured **servers** — Plex, Emby,
Jellyfin, Audiobookshelf, or a generic webhook — replacing the unreliable filesystem
monitoring built into those servers.

It is a **Python application, shipped as a Docker image**, configured from a **web UI**, with
state in **SQLite**.

> **Status: under active development.** This is a ground-up rewrite (inspired by the original
> `plex_monitor.sh` Bash script, kept in this repo as a behavioral reference — see
> [Legacy](#legacy-bash-script)). **Phase 1 is complete**: the async engine, recursive inotify
> watcher, SQLite store with encrypted secrets, the routing/debounce/dispatch pipeline, the
> **Plex** adapter, and a headless CLI all work and are covered by tests. The web UI, the other
> three server adapters, and the published Docker image land in later phases — see the
> [Roadmap](#roadmap). Until then the engine runs headless and is configured via the database
> directly.

## Why?

Media servers detect new files using filesystem notifications (inotify). Those notifications
**do not propagate over network filesystems** like NFS or SMB — so when media is added to a NAS
from another machine over the network, the server never sees the change, and new media doesn't
appear until the next scheduled full scan.

Running the watcher **directly on the machine that holds the files** (where local inotify events
always fire), then calling each server's scan/refresh API for only the folder that changed, is
the reliable fix. This project does that, and fans a single watch out to multiple servers at
once.

## How it works

```
inotify watcher ──> router ──> per-server debounce ──> dispatcher ──> server adapter(s)
   (one watch          │             │                                  (Plex today;
    per directory,      │             │                                   Emby/Jellyfin/
    recursive)          │             └─ collapse a burst (e.g. copying    Audiobookshelf/
                        │                a whole season) into one trigger  webhook upcoming)
                        └─ fan out to every (server, folder) whose path
                           prefixes the change and whose extensions match
```

- **Domain model:** `Server (1) ──< Folder (N) ──< FileType (N)`. Each folder declares the host
  path to watch, the backend library/section id, and which extensions to monitor.
- **Routing:** the watch set is the deduplicated union of all enabled folder paths. On an event,
  every `(server, folder)` whose path is a segment-prefix of the changed file *and* whose
  extensions match becomes a subscriber; the event fans out to each.
- **Per-server debounce:** `trailing` collapses a burst per `(server, scan target)` into one
  trigger after the folder settles (media-server default); `off` delivers every event (good for a
  generic webhook).
- **Scan targeting:** only Plex does native folder-targeted scans (`?path=`); Emby/Jellyfin/
  Audiobookshelf refresh a whole library. Each adapter declares which scan modes it supports.
- **inotify watch limit:** per-directory watches consume the kernel
  `fs.inotify.max_user_watches` budget. A startup gate measures what the config needs versus the
  current limit and blocks (with a clear remediation message) rather than silently under-watching.

### A note on paths

A targeted scan sends the server a path, and **that path must match how the server itself sees
the media**, not how the host sees it. If Plex runs in its own container with the library mounted
at `/data/media/tvseries`, the monitor must also refer to the media as `/data/media/tvseries`.
When running in Docker you bind-mount the source to those same paths inside the monitor
container, so everything lines up. The host-side source of each mount is independent.

## Running it (headless, Phase 1)

```bash
pip install -e .
media-scan-monitor run --no-web
```

Configuration lives in a SQLite database (servers, folders, file types, settings). The web UI
for managing it arrives in Phase 3; until then the database is populated programmatically.

Startup reads only these environment variables (everything else is in the database):

| Variable | Default | Description |
|----------|---------|-------------|
| `MSM_DB_PATH` | `/config/app.db` | SQLite database path. |
| `MSM_SECRET_KEY_FILE` | `/config/secret.key` | Fernet key file used to encrypt server secrets at rest (auto-created if absent). |
| `MSM_SECRET_KEY` | — | Fernet key provided directly (alternative to the file). |

`media-scan-monitor run` without `--no-web` currently prints a message and exits non-zero — the
web dashboard is not built yet.

> **inotify only works on local storage.** Watched paths must live on a local filesystem on the
> machine running the monitor; events do not fire for network-mounted sources.

## Development

```bash
pip install -e ".[dev]"   # install app + dev tooling

ruff check .              # lint
ruff format .             # format
mypy mediascanmonitor     # type check (strict)
pytest                    # tests
```

CI runs all four on every push/PR and must stay green. See [`CLAUDE.md`](CLAUDE.md) for the
project rules, architecture, the staged rollout, and the lint/style conventions; design specs
and implementation plans live under [`docs/`](docs/).

## Roadmap

- **Phase 1 — engine core + DB + Plex** ✅ complete.
- **Phase 2 — all server types:** Emby, Jellyfin, Audiobookshelf, and a generic webhook adapter.
- **Phase 3 — web UI + API:** password-protected dashboard, full CRUD, test buttons, live event
  feed, and live reconfiguration without a restart.
- **Phase 4 — observability & packaging:** Prometheus metrics, dashboard widgets, and the
  published multi-stage Docker image.

## Legacy bash script

The original Bash implementation (`plex_monitor.sh` and its `plex_monitor.conf` /
`docker-compose.yml` / Alpine `Dockerfile`) is preserved **only as a behavioral reference** for
the proven semantics being re-implemented. It is not maintained and should not be extended; the
Python application replaces it. The previous Bash-focused README is available in the git history.
