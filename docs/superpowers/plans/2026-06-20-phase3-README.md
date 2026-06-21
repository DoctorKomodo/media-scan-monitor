# Phase 3 — Detailed Implementation Plans (index)

Phase 3 of `media-scan-monitor` — **"Web UI + API", the headline feature** (from
[`docs/PLAN.md`](../../PLAN.md) §"Web dashboard / API" and §"Staged rollout") — puts the whole
app behind a single-password web dashboard so a user configures servers, folders and file-types
from the browser with **no DB hand-editing**, sees live engine status and a live event feed, and
every write live-`rebuild()`s the running engine (no restart).

Phase 1 built the engine/DB/pipeline; Phase 2 added every server adapter. Phase 3 wraps them in a
FastAPI app and wires `media-scan-monitor run` (without `--no-web`) to serve the engine **and** the
dashboard on one asyncio event loop.

Each sub-plan is written for an engineer with zero codebase context: bite-sized TDD tasks (failing
test → run it fails → minimal impl → run it passes → commit), exact file paths, complete code in
every step, exact commands with expected output.

## New interface contract (FROZEN)

Phase 3 **does** introduce new shared vocabulary — the web app factory, the auth/session surface,
the DI dependencies, the redacted read-schemas, the engine↔web integration points (events bus,
status), and the `engine.rebuild()` gate-recovery refinement. All of it is frozen up front in:

> [`…-phase3-00-web-interface-contract.md`](2026-06-20-phase3-00-web-interface-contract.md)

Read it before any sub-plan. The sub-plans **own** the modules the contract describes; they may not
change a frozen signature without updating the contract and every consumer.

## Documents

| # | File | Builds |
|---|------|--------|
| 00 | [contract](2026-06-20-phase3-00-web-interface-contract.md) | Frozen web vocabulary: `create_app`, DI deps, auth/session surface, read-schemas, events bus, status, rebuild gate-recovery. |
| 01 | [app + auth](2026-06-20-phase3-01-app-auth.md) | `web/{app,deps,auth,ratelimit}.py` + `observ/events_bus.py` (the `EventsBus`/`EventRecord` class) + auth router (login/logout/change-password/first-run setup) + base/login/setup templates. The FastAPI skeleton you must log into; Argon2 password in `Setting`, signed-cookie session, route guard, login rate-limit, `MSM_PASSWORD*` bootstrap. |
| 02 | [REST API](2026-06-20-phase3-02-rest-api.md) | `web/api/{servers,folders,events}.py` + `web/api_schemas.py` + `web/rebuild.py` + `repo.{get_folder,update_folder}`/`FolderUpdate`: full CRUD (secrets redacted, token-required `422`), `POST /servers/{id}/test`, the shared validate→write→rebuild cores, rebuild-on-write. |
| 03 | [engine integration](2026-06-20-phase3-03-engine-integration.md) | `observ/events_bus.py`, `web/api/system.py` (`/health`, `/ready`, `/api/status`, `PUT /api/settings/inotify-gate`, `POST /api/engine/recheck`), engine wiring (`events_bus`, blocked↔running `rebuild()` gate-recovery + `park_when_blocked`), `cli.py` `run` web+engine on one loop, `web/server.py`. |
| 04 | [dashboard + SSE](2026-06-20-phase3-04-dashboard-sse.md) | `web/pages.py` + Jinja2/htmx templates (dashboard, `/servers`, `/settings`, `/events`) + `GET /events/stream` SSE + static assets. The browser UI. |

## Dependency graph

```
Phase 1+2 (engine, repo, adapters, pipeline)
        │
        ▼
   01 app + auth ──┬─> 02 REST API ──┐
                   │                 ├─> 04 dashboard + SSE
                   └─> 03 engine integration ──┘
```

- **01** is the foundation: `create_app`, the auth/session surface, the DI deps. Everything mounts
  on the app it builds.
- **02** and **03** both depend only on **01** and are independent of each other (02 is the JSON
  CRUD surface; 03 is the engine seam + readiness + `run` wiring). They may be built in either
  order or in parallel; the single shared file each appends a router to is `web/app.py`
  (`create_app` mounts each router — trivial merge, keep all `include_router` lines).
  - **One ordering caveat:** 02's rebuild-on-write (`rebuild_engine`, contract §F) only *recovers* a
    `blocked` engine once 03's gate-recovery (§I) lands. If 02 ships first, "write while the engine is
    blocked" safely no-ops (§F catches `RuntimeError`) — correct, but the headline gate-recovery UX
    stays dark until 03. So 02 is independently shippable, but the phase's marquee behavior needs both.
- **04** (the HTML UI) depends on **02** (the forms POST to the CRUD API) and **03** (the dashboard
  renders `/api/status` + the SSE stream reads the events bus). Build it last.

## Canonical execution order

1. **01 app + auth** — must land first; nothing serves without `create_app` + the auth guard.
2. **02 REST API** — CRUD + test + rebuild-on-write.
3. **03 engine integration** — events bus, health/ready, status, `run` wiring, rebuild gate-recovery.
4. **04 dashboard + SSE** — the browser UI on top of 02+03.

The numbering is the recommended sequence. 02↔03 have no ordering constraint between them.

## Decisions locked for this phase (asked + answered at planning time)

1. **Decomposition:** four TDD sub-plans + this frozen contract, matching Phases 1 & 2.
2. **Password hashing:** **`argon2-cffi` used directly** (`argon2.PasswordHasher`), *not* `passlib`.
   `argon2-cffi==25.1.0` is already pinned in `pyproject.toml`; `passlib` is dormant (last release
   2020) and adds an abstraction layer for hash-scheme migration this app does not need.
3. **Library-id discovery dropdowns deferred past Phase 3.** The UI ships **free-text `library_id`**
   fields (exactly what the data model already stores). A discovery picker would need a new
   `list_libraries()` on the frozen `ServerAdapter` ABC and per-adapter implementations — that stays
   an enhancement for a later phase (already tracked in [`docs/FOLLOWUPS.md`](../../FOLLOWUPS.md)).

## New dependencies (verify current-stable at add-time — CLAUDE.md rule 1)

Only two runtime deps are added; both are Starlette/FastAPI companions, not new frameworks. The
versions below were verified against PyPI on 2026-06-20 — **re-verify they are still current stable
before pinning** (rule 1 forbids trusting a number from memory or from this doc):

| Package | Pin (verify) | Why |
|---|---|---|
| `itsdangerous` | `==2.2.0` | Starlette `SessionMiddleware` signs the session cookie with it; it is an optional Starlette dependency, not installed transitively. |
| `python-multipart` | `==0.0.32` | FastAPI form parsing (`Form(...)`) for the login / setup / htmx form POSTs (`application/x-www-form-urlencoded`). |

**No `sse-starlette`.** The live feed is a plain `StreamingResponse` with `media_type="text/event-stream"`
fed by an `async` generator over `EventsBus.subscribe()` — see sub-plans 03/04. Everything else the
web layer needs (`fastapi`, `uvicorn[standard]`, `jinja2`, `argon2-cffi`, `httpx` for `TestClient`,
`starlette`) is already pinned.

Add the two deps in sub-plan 01, Task 1 (the app skeleton's first commit), pinned and with the
"verified at add-time" comment alongside the existing deps in `pyproject.toml`. Then refresh the
lockfile (`uv lock`) so CI's `uv sync --locked` stays green, and commit `pyproject.toml` + `uv.lock`
together.

## Phase 3 conventions (apply to every sub-plan)

1. **Async all the way down (rule 4).** Route handlers are `async def`. The only synchronous code is
   the `Repo` (sync SQLModel) and `check_watch_limit` (blocking `os.walk` / `/proc` read); call them
   off the loop with `await asyncio.to_thread(...)` — never call a `Repo` method directly inside a
   coroutine. (This mirrors how `engine.py` already bridges to the sync repo.)
2. **Auth on every route except the allow-list.** A FastAPI dependency guards everything; the only
   unauthenticated routes are `POST /auth/login`, the first-run setup page+POST (only while no
   password is set), `GET /health`, and static assets. `/metrics` (Phase 4) and `/ready` are
   **protected**. See contract §B/§C for the exact allow-list and the two guard variants
   (API → `401`, HTML page → `303` redirect to `/login`).
3. **Secrets never leave the box (rule 5, contract invariant 3).** Read-schemas expose a
   `has_secret: bool`, **never** the token or its ciphertext. No secret in any URL, log line, SSE
   record, or template. Writes take plaintext `secret` (encrypted by the repo, as today); reads
   redact. Re-use the existing `repr=False` write-schemas (`ServerCreate`/`ServerUpdate`).
4. **Every write live-rebuilds the engine.** Any successful POST/PATCH/DELETE that changes
   server/folder/filetype config calls the rebuild-on-write helper (contract §F) before returning.
   It tolerates an engine that is `blocked`/not-yet-started (logs and no-ops) so a write never 500s
   because the watcher is detached.
5. **The inotify gate never blocks the web layer (PLAN "no-deadlock rule").** The web app always
   serves; only the engine/watcher task is gated. `/health` is always `200`; `/ready` reflects the
   engine state. A fresh install (empty config → 0 watches) is never wedged.
6. **No new server-type special-casing (rule 2).** Type-specific UI form fields are driven by the
   adapter registry's `supported_scan_modes` + a small per-type field map declared in one place
   (contract §D `SERVER_TYPE_FIELDS`); the API/router/templates never branch on a literal type name.
7. **`StrEnum`, PEP 649, ruff `E,F,I,UP,B,C4,SIM,RUF`, `mypy --strict`, line length 100, no
   `from __future__ import annotations`.** Pydantic models at every external boundary (request bodies,
   responses) — never pass raw dicts. Validate template/JSON input as the webhook adapter already does.

## Verification gate (every sub-plan, before its PR merges)

`ruff check . && ruff format --check . && mypy mediascanmonitor && pytest` — green. CI
(`.github/workflows/ci.yml`) runs the same on Python 3.14 via `uv sync --locked`. Web tests use
Starlette's `TestClient` (sync) for request/response + auth flows and `httpx.ASGITransport` +
`httpx.AsyncClient` for the async SSE smoke; no real network, no real backend (adapter `test()` is
mocked with `respx`), no real inotify (inject a fake watcher / monkeypatch `check_watch_limit`).

## After Phase 3

Phase 4 (Prometheus `/metrics`, dashboard widgets, README rewrite, repo/image rename, Dockerfile +
image smoke test) remains high-level in `docs/PLAN.md` and gets its own detailed plans once Phase 3
lands. Carried-over items for Phase 3 to consume are listed in
[`docs/FOLLOWUPS.md`](../../FOLLOWUPS.md) — the two that land here are **token-required validation**
(contract §D) and **`rebuild()` blocked↔running gate-recovery** (sub-plan 03). Remove them from the
index when done.
