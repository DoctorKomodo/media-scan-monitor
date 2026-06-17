# Plan: `media-scan-monitor` — a UI-configured media-event fan-out app

## Context

The existing project is a single Bash script (`plex_monitor.sh`) that watches media
directories on a Synology NAS with `inotifywait` and fires targeted Plex partial scans.
It is hard-wired to one backend, configured only by env vars, and has no UI.

The user wants a **brand-new app, inspired by the old one** — explicitly **no migration
or backward compatibility** with the Bash version. Confirmed direction:

- **Language: Python**, shipped as a Docker image.
- **Everything is configured from a web UI** (no hand-edited config files for normal use).
- **Data model:** N **servers** (a server is a *webhook*, *Plex*, *Emby*, *Jellyfin*, or
  *Audiobookshelf*). Each server has one or more **folders/libraries**. Each folder
  declares **which file types to monitor**.
- **Runtime model:** the app builds **one watcher** over the union of all configured
  folders (with per-folder file-type filters) and **fans out notifications to the
  subscribing servers** when a matching change occurs.
- **Persistence: SQLite** (UI-managed, relational).
- **Full dashboard:** config CRUD + per-server Test + live event feed + recent-events +
  health/metrics.
- **App authentication:** a single password gates the whole UI/API (protects the stored
  API keys and setup). No multi-user management — password-only is sufficient.

> **Project rule (applies to the whole build):** every library, module, plugin, Docker base
> image, or other dependency added must be its **current stable version**, verified at the
> time of adding (check the package registry / official docs — do **not** trust version
> numbers from memory). This is added to `CLAUDE.md` in Phase 0 and followed thereafter; all
> versions shown in this plan are placeholders to be confirmed during implementation.

Old behavior the new app should *re-implement* (as inspiration, not a compatibility
contract): inotify events `create/moved_to/delete/move`, ignore Synology system dirs
(`@eaDir`, `#snapshot`), per-target debounce so a batch of files = one scan, Plex partial
scan via `path=`, and waiting on the inotify `max_user_watches` kernel limit.

This is the **overall plan**, staged so each phase becomes its own detailed plan / PR.

---

## Project rules / guidelines (written into `CLAUDE.md` in Phase 0)

For long-term maintainability of a small-but-real app:

- **Dependencies:** current stable versions only, verified at add-time (never from memory);
  pin exact versions; keep the set small and justify each addition; prefer stdlib/existing deps.
- **Extensibility:** adding a server type = **one new file** implementing the `ServerAdapter`
  ABC + a registry entry + tests. The watcher/pipeline must never special-case a backend.
- **Structure & typing:** respect module boundaries (one responsibility each); full type hints
  with `mypy` clean; `ruff` for lint+format; validate every external boundary (UI/API/backend
  responses) with Pydantic/SQLModel — no raw dicts passed around.
- **Async discipline:** async all the way down the I/O path; no blocking calls in the loop.
- **Security:** never log secrets; redact in API; encrypt at rest; keep tokens out of logged
  URLs; container runs non-root.
- **Testing:** test pyramid (many unit, few integration, minimal e2e); every adapter + the
  routing/debounce logic covered; CI (`ruff`+`mypy`+`pytest`) green before merge.
- **Data/migrations:** DB schema changes go through an explicit migration step — never
  silently break an existing `app.db`.
- **Resilience & observability:** fail fast on startup/config errors; isolate per-server
  runtime failures; structured logging + metrics for silent-failure-prone paths.
- **Process & docs:** small single-purpose PRs aligned to the phases; keep `README.md` and
  `CLAUDE.md` in sync; document each backend's API quirks alongside its adapter.

---

## Domain model

```
Server (1) ──< Folder (N) ──< FileType (N)     [folder.extensions]
   │
   └─ type: webhook | plex | emby | jellyfin | audiobookshelf
      connection (base_url/url, method, verify_tls, timeout)
      auth (token / api_key / bearer; stored encrypted)
      scan_mode: targeted | library
      debounce_mode: off | trailing      ← PER SERVER (off = fire every event)
      debounce_window_seconds: int       ← only when trailing
      enabled, retry policy

Folder:
   path            (host path watched, e.g. /data/media/tvseries)
   library_id      (the backend's library/section id for this folder; n/a for webhook)
   extensions      (file types to monitor for THIS folder, e.g. mkv,mp4,srt)
   enabled
```

Routing semantics (the heart of the design):

- **Watch set** = the *deduplicated union* of all enabled folder paths across all enabled
  servers. Per watched path, the watcher's effective filter = the *union* of the file-type
  filters of every folder subscribing to that path (so it captures any event a subscriber
  cares about).
- On a filesystem event: find every `(server, folder)` whose `path` is a prefix of the
  changed file **and** whose `extensions` match the file → those servers are the
  **subscribers** for this event → fan out to each.
- The "scan path" sent to a backend is computed per subscriber (folder root + first path
  segment after it, matching the proven Plex behavior) for `targeted` servers; `library`
  servers refresh the whole mapped library id.

This cleanly supports: the same folder feeding multiple servers; a webhook server that just
wants raw events; and different file-type filters per server even on the same path.

**Per-server debounce (each subscriber decides independently):** every server carries its own
debounce policy, applied *after* routing, per subscriber, so the same filesystem event can be
coalesced for one server and delivered immediately to another:

- `off` → no debounce: **every** matching event is dispatched to that server (e.g. a generic
  webhook that wants the full stream of file changes it subscribes to).
- `trailing` (window N s) → re-implements the proven NAS behavior: collapse a burst of events
  for the same `scan_key` into a single trigger after the folder settles. Sensible per-type
  defaults (e.g. Plex/Emby/Jellyfin/ABS default to `trailing`; webhook defaults to `off`),
  all overridable in the UI.

The debouncer is keyed per `(server_id, scan_key)`, and a server in `off` mode simply bypasses
the debouncer entirely — its events go straight to the dispatcher.

---

## Server-type API reference (verified June 2026)

| Type | Trigger call | Auth | Targeted folder scan? |
|---|---|---|---|
| **Plex** | `GET /library/sections/{id}/refresh?path={enc}` | `X-Plex-Token` header | **Yes (native)** |
| **Emby** | `POST /Items/{libId}/Refresh?Recursive=true` (or `/Library/Refresh` for all) | `X-Emby-Token` header | No → `library` mode |
| **Jellyfin** | `POST /Items/{collectionFolderId}/Refresh?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default`; id from `GET /Library/VirtualFolders` (or `/Library/Refresh`) | `Authorization: MediaBrowser Token="..."` | No → `library` mode |
| **Audiobookshelf** | `POST /api/libraries/{libraryId}/scan` (`?force=1` optional) | `Authorization: Bearer {token}` | Library scan only |
| **Webhook** | configurable method/URL/headers + Jinja2-templated JSON body (vars: `event, file_path, scan_path, top_folder, server, library_id`) | per-config | N/A |

Each adapter declares its supported `scan_mode`s; UI only offers valid modes per type.

---

## Architecture

Single installable Python package, async core (inotify + httpx fan-out + FastAPI share one
event loop). Watcher + web run in **one process** by default.

```
mediascanmonitor/
  __main__.py / cli.py          # entrypoint: run (web+engine) | --no-web
  db/
    models.py                   # SQLModel: Server, Folder, FileType (+ relationships)
    session.py                  # engine/session, SQLite at /config/app.db
    repo.py                     # CRUD helpers used by API + engine
    crypto.py                   # encrypt/decrypt secrets at rest (Fernet, key from /config or secret)
  config/
    runtime.py                  # builds RuntimeConfig (watch set + routing table) from DB
    defaults.py                 # default ignore dirs, suggested extension presets
  watcher/
    base.py                     # WatcherBackend protocol -> async FsEvent stream
    inotify_backend.py          # asyncinotify, recursive (dynamic watch add/remove)
    watch_limit.py              # /proc/sys/fs/inotify gate
  pipeline/
    events.py                   # FsEvent, ScanRequest dataclasses
    router.py                   # event -> matching (server,folder) subscribers
    filters.py                  # per-folder extension + ignore-dir matching
    debounce.py                 # per-server policy (off|trailing); keyed per (server, scan_key)
    dispatcher.py               # fan-out, per-server retry/backoff/timeout, isolation
  servers/                      # "server" = target adapter
    base.py                     # ServerAdapter ABC: trigger(req), test()
    registry.py                 # type -> adapter class
    http.py                     # shared httpx client + tenacity retry helper
    plex.py / emby.py / jellyfin.py / audiobookshelf.py / webhook.py
  observ/
    logging.py                  # structlog JSON logging
    metrics.py                  # prometheus-client counters/histograms
    events_bus.py               # async pub/sub + recent-events ring buffer (for SSE/UI)
  engine.py                     # owns watcher+pipeline; rebuild() on config change
  web/
    app.py                      # FastAPI factory; lifespan starts engine
    api.py                      # REST routers (servers, folders, test, events, health, metrics)
    sse.py                      # GET /api/events/stream
    templates/ + static/        # Jinja2 + htmx dashboard
```

Key choices & justifications:

- **Watcher: `asyncinotify`** — async-native (no thread bridge), keeps the raw
  `max_user_watches` model the NAS depends on. Raw inotify isn't recursive, so the backend
  adds a watch per directory and dynamically adds/removes on subdir create/delete; on a new
  subdir it scans existing contents to close the attach-window race. (`watcher/base.py` is a
  protocol so `watchdog` could be swapped for non-Linux dev.)
- **Live reconfiguration is first-class:** when the UI changes servers/folders, the API
  commits to SQLite and calls `engine.rebuild()`, which diffs the new watch set against the
  current one (add/remove watches) and swaps the routing table — **no restart**. This
  replaces the old env-var-at-startup model entirely.
- **Dispatcher** fans out to all subscriber servers, each with its own timeout +
  retry/backoff (`httpx` + `tenacity`); per-server failure is isolated and recorded.
- **Debounce is a per-server policy** applied after routing: `off` bypasses the debouncer so
  the server gets every matching event (e.g. a generic webhook); `trailing` collapses a burst
  keyed per `(server_id, scan_key)` — `scan_key` = scan_path for targeted servers, library_id
  for library-mode servers (so library refreshes collapse a burst). Defaults per type,
  overridable in the UI.
- **Web: FastAPI + Jinja2 + htmx** — async-native, free OpenAPI explorer, trivial SSE, no
  SPA build/node toolchain (good for homelab). FastAPI `lifespan` starts the engine as a
  supervised background task.

---

## Persistence (SQLite)

- File at `/config/app.db` (mounted volume). `SQLModel`/SQLAlchemy with a tiny migration
  step (`SQLModel.metadata.create_all`; add Alembic only if/when schema churns).
- Tables: `server`, `folder` (FK→server), `filetype` (FK→folder) — or store extensions as a
  small list column on `folder`; final shape decided in Phase 1. Plus a `setting` table for
  the app password hash and any singletons.
- **Secrets encrypted at rest** (`db/crypto.py`, Fernet) with a key from `/config/secret.key`
  (auto-generated, chmod 600) or an env/Docker-secret-provided key; never returned in plain
  text by the API.

---

## Web dashboard / API

Server-rendered Jinja2 + htmx; SSE for the live feed.

- **Pages:** `/` dashboard (engine status, live watch-count: current kernel limit vs needed vs
  configured target, with a recommended `sysctl` line when near the limit; per-server health
  cards, recent events); a `/settings` page exposing **`required_inotify_watches`** (default
  131072, `0` to skip the gate) and other singletons; `/servers` list + add/edit (type-specific
  forms incl. a per-server
  **debounce control** — off vs trailing + window seconds; per-server folder sub-forms with
  extension pickers); `/servers/{id}` detail + **Test**; `/events` (live SSE +
  searchable recent events).
- **REST API (`/api`):**
  - `GET/POST/PATCH/DELETE /servers[/{id}]` (PATCH = enable/disable); secrets redacted on read.
  - `GET/POST/PATCH/DELETE /servers/{id}/folders[/{fid}]` (path, library_id, extensions, enabled).
  - `POST /servers/{id}/test` → runs `ServerAdapter.test()` (auth + reachability).
  - `GET /events/recent?limit=` and `GET /events/stream` (SSE) from `events_bus`.
  - `GET /health` (liveness) + `GET /ready` (DB reachable, watcher attached, inotify gate passed).
  - `GET /metrics` (Prometheus).
  - `POST /auth/login` / `POST /auth/logout`; first-run setup to set the password if unset.
  - All writes trigger `engine.rebuild()`.
- **App auth (required):** single password gates the entire UI and API. Password hashed with
  Argon2 (`argon2-cffi` / `passlib`) and stored in the `setting` table; login issues a signed
  session cookie (Starlette `SessionMiddleware`, secret from `/config/secret.key`). A FastAPI
  dependency protects every route except `/auth/login`, the setup page, and `/health`.
  Bootstrap password via `MSM_PASSWORD`/`MSM_PASSWORD_FILE` on first run, or a first-run setup
  screen; changeable later in the UI. Lightweight rate-limiting on login. `/metrics` is
  protected too (or bound to localhost).

---

## Deployment

- **Dockerfile:** multi-stage `python:3.14-slim` (build venv → slim runtime), `tzdata`, run
  as non-root. Deps (pinned in `pyproject.toml`): `fastapi`, `uvicorn[standard]`, `httpx`,
  `pydantic>=2`, `sqlmodel`, `asyncinotify`, `jinja2`, `tenacity`, `structlog`,
  `prometheus-client`, `cryptography`, `argon2-cffi` (or `passlib[argon2]`). No bash/curl/
  inotify-tools. **All versions pinned to the current stable release, verified at add-time.**
  - **When this lands (install via the committed lockfile, matching CI byte-for-byte):** bring
    `uv` into the build stage (`COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv`), `COPY`
    `pyproject.toml` + `uv.lock` first, then `RUN uv sync --locked --no-editable` (production —
    **omit** `--extra dev` so the dev tools are excluded), and copy the resulting `.venv` into
    the slim runtime stage. The lockfile — not loose `pip install` — is the source of truth, so
    the image, CI, and the dev box all resolve identically.
- **docker-compose:** `./config:/config` **read-write** (SQLite db + secret key + state),
  `ports: ["8080:8080"]`, and **read-only** media bind-mounts (`/volume2/media:/data/media:ro`).
  The monitor only ever *reads* the watched tree (inotify is a kernel-side watch + directory
  reads) and never creates/moves/deletes media, so `:ro` is the default — least privilege means
  the network-facing app physically cannot corrupt the library. Sources MUST still be on local
  `/volume2` (inotify does not work over network mounts). No `PLEX_*` env — everything is
  configured in the UI; only optional bootstrap (e.g. `MSM_PASSWORD_FILE` to seed the app
  password on first run).
- **inotify watch limit (`fs.inotify.max_user_watches`):** the main app stays **non-root and
  read-only** w.r.t. the kernel — it never writes the sysctl; it only *checks* it (the `/ready`
  gate blocks until the limit is sufficient and the dashboard shows current-vs-required). Two
  supported ways to actually raise it:
  1. **Host-level (sanest default):** a persistent `sysctl.d` drop-in or the existing root boot
     task sets `fs.inotify.max_user_watches=131072` once. Zero privilege in the app container.
  2. **One-shot privileged init sidecar (opt-in convenience):** a tiny separate compose service
     that runs `sysctl -w fs.inotify.max_user_watches=131072` and **exits** before the main
     service starts, so the long-lived web app never holds elevated privileges. This is a
     well-established pattern — Elasticsearch/OpenSearch do exactly this for `vm.max_map_count`,
     Bitnami's Helm charts ship a configurable `sysctlImage` init container for the same job, and
     CNI plugins (Cilium/Calico) use privileged init containers for networking sysctls. Document
     it loudly as **privileged** and note it writes a **host-global** value (on the Synology
     target kernel this tunable is global, not per-container), so it changes the limit for the
     whole host. Offered as an escape hatch for users who'd rather not touch the host; the
     host-level option remains the documented default.

  **The required value is tunable, not hard-coded.** `131072` (`2^17`) is the proven default
  carried over from the Bash script (`REQUIRED_INOTIFY_WATCHES`), **not** a kernel ceiling —
  `max_user_watches` is bounded only by memory (~1 KB of unswappable kernel memory per watch, so
  131072 ≈ ~135 MB worst-case), and large libraries commonly run 524288 or 1048576. So:
  - Expose **`required_inotify_watches`** as a persisted setting (in the `setting` table, editable
    in the UI; bootstrappable via env for first run), default `131072`, with `0` to skip the gate
    entirely — same semantics as the Bash env var.
  - Because the app manages watches **per directory**, it already *knows* the live watch count and
    the count it needs (≈ number of watched dirs). The readiness gate checks the kernel's current
    `max_user_watches` against `max(required_inotify_watches, needed + headroom)`, and the
    dashboard surfaces **current vs needed vs configured target** so the value can be tuned from
    real data rather than guessed — and the app can *recommend* a higher target (and the exact
    `sysctl`/boot-task line to apply) when a big library approaches the limit.
- **CI:** `ci.yml` installs via `uv sync --locked --extra dev` (lockfile-enforced) on Python
  3.14, then runs `ruff` + `mypy` + `pytest`. When the new image lands, update
  `docker-build.yml`: change path filters from `plex_monitor.sh` to `mediascanmonitor/**` +
  `pyproject.toml` + `uv.lock` + `Dockerfile`, ensure `uv.lock` is in the build context, keep
  the multi-arch GHCR publish, and add an image smoke test (`docker run … --help`).

---

## Staged rollout (separately-shippable PRs)

- **Phase 0 — Scaffolding & rules:** rewrite `CLAUDE.md` project rules for the new app —
  including the **"current stable version only, verified at add-time, never from memory"**
  rule — plus `pyproject.toml`, package skeleton, ruff/mypy/pytest, `ci.yml`.
- **Phase 1 — Engine core + DB + Plex:** SQLModel schema (`server/folder/filetype`),
  `db/repo` + `crypto`, runtime config builder + routing, recursive `inotify_backend` +
  `watch_limit`, pipeline (router/filters/debounce/dispatcher), `servers/{base,registry,http,plex}`,
  `engine.rebuild()`, logging, headless `cli` run. Deliverable: configure (seeded) Plex
  servers/folders in the DB and get targeted partial scans with debounce.
- **Phase 2 — All server types:** `servers/{emby,jellyfin,audiobookshelf,webhook}` with
  per-type validation, `scan_mode` handling, webhook templating, `ServerAdapter.test()`.
  Deliverable: simultaneous fan-out to mixed server types.
- **Phase 3 — Web UI + API (the headline feature):** FastAPI app, **password auth**
  (login/logout, session cookie, first-run setup, route-guard dependency), full CRUD for
  servers/folders/file-types, Test buttons, dashboard, live SSE feed, recent events,
  health/ready; live `engine.rebuild()` on every write. Deliverable: app fully usable
  from the browser behind a password, no DB hand-editing.
- **Phase 4 — Observability & polish:** Prometheus `/metrics`, dashboard widgets (watch
  count, per-server health/latency), extension presets, optional auth hardening, README
  rewrite, repo/image rename, image smoke test.

---

## Verification

- **Adapters (unit):** `pytest` + `respx` (httpx mock) — assert exact URL/method/headers/body
  per type (Plex `path=` encoding, Jellyfin `MediaBrowser Token=`, ABS `Bearer`, webhook
  template), success/failure classification, retry/backoff (mock sleep), timeouts.
- **DB/repo:** CRUD + cascade deletes (server→folders→filetypes), secret encrypt/decrypt
  round-trip, redaction on read.
- **Routing/pipeline:** synthetic `FsEvent`s → assert correct subscriber set (prefix +
  per-folder extension match, ignore-dirs), scan-path computation, and per-server debounce
  with a fake clock; verify same folder fanning out to multiple servers.
- **Engine rebuild:** mutate config → assert watch set diff (add/remove) and routing swap
  with no dropped events.
- **Watcher (integration, Linux-only):** real `asyncinotify` on `tmp_path` — files + newly
  created subdirs; assert recursive dynamic watch add/remove.
- **Web:** FastAPI `TestClient` for CRUD/test/health, SSE smoke, that writes invoke
  `rebuild()`, and **auth** (unauthenticated requests rejected, login/session flow, password
  hash round-trip, first-run setup).
- **E2E:** docker-compose rig — container watching a tmp dir + stub HTTP servers
  impersonating Plex/Emby/etc.; create config via API, drop a file, assert the right calls
  land. Plus CI image smoke test.

---

## Risks / trade-offs

1. **inotify isn't recursive natively** — per-dir watch management + dynamic add on subdir
   create (scan new dir contents to avoid the attach race). Biggest new code area.
2. **Live rebuild correctness** — adding/removing watches and swapping routing on UI edits
   without dropping or double-firing events; pin with rebuild tests.
3. **Watch limits on the NAS** — per-dir watches consume `fs.inotify.max_user_watches`; keep
   the read-only gate (app never writes the sysctl), surface live count, and document both ways
   to raise it: host-level `sysctl.d`/boot task (default) or an opt-in one-shot privileged init
   sidecar (precedent: Elasticsearch `vm.max_map_count`, Bitnami `sysctlImage`). The sidecar is
   privileged and writes a host-global value — call that out wherever it's offered. The required
   target is a tunable setting (`required_inotify_watches`, default 131072 — not a kernel ceiling,
   only memory-bound), and the gate compares the kernel value against `max(target, needed)` using
   the app's own per-directory watch count so the value is data-driven, not guessed.
4. **inotify over bind mounts** — sources MUST be local `/volume2`; document loudly, warn on
   "no events ever seen".
5. **Debounce semantics** — per-server policy: `off` (deliver every event, e.g. webhooks) vs
   `trailing` (collapse a burst); re-implement the trailing-delay behavior and pin both modes
   with fake-clock tests (assert `off` fans out every event, `trailing` coalesces).
6. **Secrets at rest** — encrypt in SQLite, protect the key file, never return plaintext or
   render secrets in forms (set/unset + replace-only); keep Plex token out of logged URLs.
7. **One process vs two** — single process is simplest (default); isolate the engine as a
   supervised task and offer `--no-web` for separation.
8. **Targeted-scan asymmetry** — only Plex (and an optional Emby/Jellyfin plugin) does folder
   targeting; others do library refresh. UI offers only valid `scan_mode`s and explains it.

---

## Critical files (reference / inspiration)

- `plex_monitor.sh` — proven semantics to re-implement: events, ignore dirs, scan-path
  computation, debounce, Plex partial-scan call, inotify-limit gate.
- `Dockerfile`, `docker-compose.yml` — rewrite for Python + config volume + web port.
- `.github/workflows/docker-build.yml` — update path filters; add lint/type/test workflow.

---

## Appendix — Architecture validation: path mapping (NOT scheduled; exploration only)

A future-feature stress-test, kept here to document that the architecture absorbs a
cross-cutting transform cleanly. **Not part of any phase above.**

**Concern:** the host path the watcher sees (`/data/media/tvseries/Shoresy`) ≠ the path the
target server sees (e.g. a Windows-hosted Plex against a UNC share needs
`\\nas\media\tvseries\Shoresy`). It's a per-target transform of `scan_path` at dispatch time;
only path-bearing calls (targeted scans, webhook bodies) need it — `library`-mode refreshes
don't.

**Unchanged (the validation):** watcher/watch-set/inotify backend, router/filters, debounce
(stays keyed on the *host* scan_key), `engine.rebuild()` diffing, and the `ServerAdapter` ABC
signature. Path *style* is data, not code — no per-backend special-casing.

**Localized changes:**
1. `pipeline/pathmap.py` (new, pure): `map_path(host_path, rules, style) -> str` —
   longest-prefix substitution + separator/UNC/drive-letter reformatting; fully unit-testable.
2. `pipeline/events.py`: `ScanRequest` carries `host_scan_path` (debounce/logging) +
   `remote_scan_path` (what the adapter sends).
3. `pipeline/dispatcher.py`: one mapper call to populate `remote_scan_path` before the adapter.
4. `db/models.py`: per-server `PathMapping` (source_prefix, target_prefix, path_style) +
   migration. `config/runtime.py` carries rules into the routing entry.
5. Adapters: read `remote_scan_path`; Plex URL-encoder handles backslashes/UNC. Webhook
   exposes both `host_path` and `remote_path` template vars.
6. UI/API: per-server "Path mappings" sub-form + Test preview of a translated path.

**Edge cases:** case-insensitive Windows matching, trailing-separator normalization, mixed
separators, UNC `\\host\share` vs drive `C:\`, and Plex's own native path mappings (redundant
for Plex, valuable for webhooks/stricter backends).

**Verdict:** passes — one pure module + one dispatcher hook + config/UI plumbing, no changes to
watcher, router, debounce, or adapter contract.
