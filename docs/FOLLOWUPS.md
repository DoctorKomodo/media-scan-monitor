# Deferred follow-ups (index)

Cross-phase items not done in the phase that surfaced them. Detail + rationale live in the
linked plan/contract section (single source of truth); this is just the scannable list.
Add a pointer here when you defer something to a later phase; **remove the item when it's done.**

## Phase 3 — web UI

### Deferred UI polish (sub-plan 04)

- [ ] Dashboard/events **live-refresh** polish (poll `/api/status`, htmx SSE extension) — baseline
      ships server-rendered status + a plain `EventSource` feed. → phase3-04 dashboard plan
- [ ] Folder picker assumes **one folder editor per page**: `_folder_picker.html` is included inside
      `_folder_editor.html` (so the `<dialog>` / `id="fs-listing"` would duplicate if a second editor
      ever rendered), while the picker JS binds all `[data-folder-editor]` to a single shared dialog.
      Correct today; if a page ever shows two editors, hoist the dialog to one page-level include with a
      unique listing target per editor. → docs/superpowers/specs/2026-06-22-folder-picker-design.md

### Cosmetic / low-priority (Phase 3 review carry-overs)

- [ ] `Engine.rebuild()` logs `added=[]` on a `blocked→running` recovery even though the watcher's
      effective roots genuinely went 0→N (it diffs against config paths, which were already the
      desired set while blocked). Log fidelity only — no behavioral effect. Track last-applied roots
      if the log accuracy matters. (Related, sanctioned: `start()` now builds adapters/clients before
      the gate even on headless-blocked, then closes them — I/O-free per contract §I.) → phase3-03 Task 2

## Tooling / hygiene

- [ ] Migrate `httpx` → `httpx2`: Starlette 1.3.x's `TestClient` prefers `httpx2`, falling back to
      `httpx` with a `StarletteDeprecationWarning` (suppressed via a narrow `filterwarnings` ignore in
      `pyproject.toml`; remove the ignore once migrated). **Researched 2026-06-21:** `httpx2` is the
      legitimate successor — authored by Tom Christie, now maintained by Pydantic
      (github.com/pydantic/httpx2, latest 2.4.0) as `encode/httpx` goes quiet; API-compatible with our
      usage. **BLOCKER:** `respx==0.23.1` (latest) supports only `httpx` (`httpx>=0.25.0`) and patches
      httpx internals — it has NO httpx2 support. Our 9 `respx`-mocked adapter tests + the server
      adapters use `httpx` in production, so a swap breaks the test layer until either respx ships
      httpx2 support or those tests move to `httpx2.MockTransport`. Documented dep blocker (rule 1) —
      revisit when respx supports httpx2. → phase3-01 Task 4 conftest

## Later — targeted scans for the non-Plex backends

- [ ] Per-folder targeted scans for Emby/Jellyfin/Audiobookshelf (Phase 2 ships library-refresh
      only, by deliberate choice). Verified endpoints: Emby/Jellyfin `POST /Library/Media/Updated`
      (`{Updates:[{Path,UpdateType}]}`); Audiobookshelf `POST /api/watcher/update`
      (`{libraryId,path,type}`, ABS ≥2.9.0 — note reliability bug advplyr/audiobookshelf#3018).
      Adding it = extend each adapter's `supported_scan_modes` with `targeted` + a path-targeted
      `trigger()` branch; the UI then offers the mode. → phase2-README convention 2

## Unscheduled — exploratory (not assigned to any phase)

- [ ] Path mapping (host→consumer path remapping), incl. the webhook `remote_path` template var.
      The watcher sees the **container's bind-mount path** (e.g. `/data/media/...`); a consumer may
      mount the same content elsewhere (e.g. a Windows Plex wanting `\\nas\media\...`). Phase 2's
      webhook exposes only `host_path` (the watcher's path); there is no mapping field in the data
      model yet. The design treats this as an architecture-validation exercise, **explicitly not
      scheduled into any phase** — promote it to a phase only on an actual request. → docs/PLAN.md
      "Appendix — Architecture validation: path mapping (NOT scheduled; exploration only)"

## Phase 4 — deployment & release readiness

- [ ] inotify resilience runbook: privileged `IN_Q_OVERFLOW` / `ENOSPC` reproduction (lower
      `fs.inotify.max_queued_events` / `max_user_watches` in a throwaway container) + a
      real-NAS bulk-import smoke. Can't run in CI; surfaced in README troubleshooting section
      instead. → 04 self-review

### Phase 4b — observability polish (deferred; do not build in Phase 4)

These items were in scope in `docs/PLAN.md` Phase 4 but were deliberately descoped so the
deployment / release-readiness half ships first. Promote to a phase when there is a concrete
request.

- [ ] **Prometheus `/metrics` endpoint** — structured per-server dispatch metrics (success/fail
      count, latency histograms). Deferred from Phase 4 per `docs/superpowers/specs/2026-06-21-phase4-deployment-design.md`
      §Scope. → Phase 4b
- [ ] **Dashboard widgets** — watch count, per-server health / latency / last-dispatch shown on
      the dashboard and server detail pages. Requires the `/metrics` data or a `/api/status`
      extension. → Phase 4b
- [ ] **Optional auth hardening** — e.g. TOTP second factor, session expiry, or per-IP login
      lockout persistence (currently in-memory only). → Phase 4b
- [ ] **GitHub-repo + local-dir rename at cutover** — the published image is already
      `media-scan-monitor` (decoupled in `docker-build.yml`), but the GitHub repository is still
      `syno_plex_change_monitor` and the local working directory matches. Rename both when `app-v2`
      merges to `main` as the "viable replacement" cutover. Document the remote-URL update step.
      → phase4-deployment-design §2 locked decision 2

## During implementation (heads-up, not deferred work)

- [ ] Alembic `compare_metadata` may flag spurious enum / SQLite type-affinity diffs on first
      build — add a `compare_type` / `include_object` filter in `migrations/env.py` if so. → 01 Task 3

## Ongoing

- [ ] Keep `_redact_secrets` `SENSITIVE_KEYS` current as new sensitive log fields appear. → 06 Task 1
- [ ] `dev.db` (Alembic autogenerate scratch file) — gitignore it if it becomes a nuisance.
