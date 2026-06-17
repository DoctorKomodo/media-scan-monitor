# media-scan-monitor — Development Guide

## Purpose

`media-scan-monitor` watches media directories (originally on a Synology NAS) and triggers
**targeted scan/refresh events** on media servers when files are added, moved, or deleted. It
replaces unreliable built-in filesystem monitoring with a single inotify watcher that **fans
out** notifications to any number of configured **servers** (Plex, Emby, Jellyfin,
Audiobookshelf, or a generic webhook).

It is a **Python application, shipped as a Docker image**, configured entirely from a **web
UI** (no hand-edited config files for normal use), with state in **SQLite**.

> This is a ground-up rewrite *inspired by* the original `plex_monitor.sh` Bash script. There
> is **no migration or backward-compatibility** requirement with the Bash version. The old
> script and its docs remain in the repo only as a behavioral reference (see "Legacy
> reference" below).

---

## Project rules (MUST follow)

These override default behavior and apply to every change in this repo.

1. **Dependencies — current stable, verified at add-time, never from memory.** Before adding or
   bumping *any* library, tool, plugin, or Docker base image, check the package registry /
   official docs for the current stable version and pin that exact version. Do **not** trust
   version numbers from training data. Keep the dependency set small; prefer the stdlib or an
   existing dependency before adding a new one, and justify every addition. **This rule also
   governs the Python interpreter version itself** — target the current stable Python (verified
   against python.org, not defaulted from memory) and keep it in sync everywhere it is declared:
   `requires-python`, the `ruff`/`mypy` targets, the CI `python-version`, and the Docker base
   image. The only reason to lag the newest stable is a *documented* blocker (e.g. a required
   dependency has no wheel for it yet); record that reason here if it ever applies.
2. **Extensibility — adding a server type is one file.** A new backend = one new module under
   `mediascanmonitor/servers/` implementing the `ServerAdapter` ABC + a registry entry + its
   tests. The watcher and pipeline must **never** special-case a specific backend. Backend
   quirks live in that backend's adapter.
3. **Structure & typing.** One responsibility per module; respect the boundaries below. Full
   type hints; `mypy --strict` clean. `ruff` for both lint and format. Validate every external
   boundary (UI input, REST I/O, backend responses) with Pydantic/SQLModel — don't pass raw
   dicts around.
4. **Async discipline.** Async all the way down the I/O path (inotify, httpx fan-out, FastAPI
   share one event loop). No blocking calls in the event loop.
5. **Security.** Never log secrets; redact them in API responses; encrypt secrets at rest; keep
   tokens out of logged URLs. The container runs as a non-root user.
6. **Testing — pyramid.** Many unit tests (adapters, router, filters, debounce), a few
   integration tests (real inotify, FastAPI TestClient), minimal e2e. Every adapter and the
   routing/debounce logic must be covered. CI (`ruff` + `mypy` + `pytest`) must be green before
   merge.
7. **Data / migrations.** DB schema changes go through an explicit migration step — never
   silently break an existing `app.db`.
8. **Resilience & observability.** Fail fast on startup/config errors; isolate per-server
   runtime failures (one dead backend never blocks the others); structured logging + metrics
   for paths that otherwise fail silently.
9. **Process & docs.** Small, single-purpose PRs aligned to the phases below. Keep `README.md`
   and this file in sync with reality. Document each backend's API quirks beside its adapter.

---

## Architecture

Single installable Python package; one async process by default (watcher + web on one event
loop). `--no-web` runs the engine headless.

```
mediascanmonitor/
  cli.py / __main__.py    # entrypoint: `run` (web+engine) | `run --no-web`
  db/                     # SQLModel models, session, repo, secret crypto (Fernet)
  config/                 # RuntimeConfig builder (watch set + routing table), defaults
  watcher/                # async inotify backend (recursive via dynamic watches), watch-limit gate
  pipeline/               # events, router, filters, per-server debounce, dispatcher (fan-out)
  servers/                # ServerAdapter ABC + registry + per-type adapters (plex, emby, ...)
  observ/                 # structlog logging, prometheus metrics, events bus (SSE ring buffer)
  engine.py               # owns watcher+pipeline; rebuild() on config change (no restart)
  web/                    # FastAPI app, REST API, SSE, Jinja2+htmx dashboard, password auth
```

### Core concepts

- **Domain model:** `Server (1) ──< Folder (N) ──< FileType (N)`. A *server* is a webhook,
  Plex, Emby, Jellyfin, or Audiobookshelf target. Each folder declares the host path watched,
  the backend library/section id, and which file extensions to monitor.
- **Routing:** the watch set is the deduplicated union of all enabled folder paths. On a
  filesystem event, every `(server, folder)` whose path is a prefix of the changed file **and**
  whose extensions match becomes a *subscriber*; the event fans out to each.
- **Per-server debounce:** each server has its own policy applied *after* routing. `off` =
  deliver every matching event (e.g. a generic webhook wants the full stream); `trailing` =
  collapse a burst keyed per `(server_id, scan_key)` into one trigger (media-server default).
- **Live reconfiguration:** UI writes commit to SQLite and call `engine.rebuild()`, which diffs
  the watch set (add/remove watches) and swaps the routing table — no restart.
- **Scan targeting asymmetry:** only Plex does native folder-targeted scans (`?path=`); Emby/
  Jellyfin/Audiobookshelf refresh a whole library. Each adapter declares its supported
  `scan_mode`s; the UI only offers valid ones.

---

## Staged rollout (one PR per phase)

> Full design rationale, domain model, server-type API reference, and verification strategy
> live in [`docs/PLAN.md`](docs/PLAN.md).

- **Phase 0 — Scaffolding & rules (this PR):** `CLAUDE.md`, `pyproject.toml`, package skeleton,
  ruff/mypy/pytest config, `ci.yml`, CLI skeleton.
- **Phase 1 — Engine core + DB + Plex:** SQLModel schema, repo + crypto, runtime/routing,
  recursive inotify backend + watch-limit gate, pipeline (router/filters/debounce/dispatcher),
  `servers/{base,registry,http,plex}`, `engine.rebuild()`, headless `run`.
- **Phase 2 — All server types:** `servers/{emby,jellyfin,audiobookshelf,webhook}` + `test()`.
- **Phase 3 — Web UI + API:** FastAPI app, password auth, full CRUD, Test buttons, dashboard,
  live SSE feed, health/ready, live `rebuild()` on every write.
- **Phase 4 — Observability & polish:** Prometheus `/metrics`, dashboard widgets, README
  rewrite, repo/image rename, image smoke test.

---

## Development

```bash
pip install -e ".[dev]"   # install app + dev tooling

ruff check .              # lint
ruff format .             # format
mypy mediascanmonitor     # type check
pytest                    # tests
```

CI runs all four on every push/PR (`.github/workflows/ci.yml`). Keep it green.

---

## Deployment (target shape; built out across phases)

- **Image:** multi-stage `python:3.14-slim`, runs as non-root, `tzdata` for log timestamps.
- **Volumes:** `./config:/config` holds `app.db` + the Fernet secret key. Media sources must be
  bind-mounted from **local** storage (inotify does not work over network mounts).
- **Config:** everything in the UI behind a single app password. Only optional bootstrap env is
  the first-run password (`MSM_PASSWORD` / `MSM_PASSWORD_FILE`).
- **inotify watch limit:** still set at the host level (root boot task on Synology:
  `echo 131072 > /proc/sys/fs/inotify/max_user_watches`); the app's readiness gate blocks until
  the limit is sufficient and the dashboard surfaces current-vs-required.

---

## Legacy reference (do not extend)

The original Bash implementation is preserved purely as a behavioral reference for the proven
semantics being re-implemented:

- `plex_monitor.sh` — inotify events (`create`/`moved_to`/`delete`/`move`), ignore dirs
  (`@eaDir`, `#snapshot`), scan-path computation (library root + first folder segment), the
  debounce strategy, the Plex partial-scan call (`/library/sections/{id}/refresh?path=`), and
  the inotify-limit wait.
- `plex_monitor.conf`, `plex_token.txt.example`, the Alpine `Dockerfile`, and `docker-compose.yml`
  are legacy artifacts; the Python app replaces them (Dockerfile/compose rewritten in later phases).
