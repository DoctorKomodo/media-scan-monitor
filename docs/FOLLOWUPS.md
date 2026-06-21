# Deferred follow-ups (index)

Cross-phase items not done in the phase that surfaced them. Detail + rationale live in the
linked plan/contract section (single source of truth); this is just the scannable list.
Add a pointer here when you defer something to a later phase; **remove the item when it's done.**

## Phase 3 — web UI

- [x] ~~`Engine.rebuild()` full `blocked`↔`running` inotify-gate recovery~~ — DONE in phase3-03 §I
      (`_gate_ok` + `start(park_when_blocked)` + gate-aware `rebuild()`; blocked = zero-root watcher,
      recovers with no restart). Opus-reviewed; four transitions tested.
- [x] ~~Require a token when saving an auth-required server~~ — DONE: the shared write-cores
      (`web/writes.py`, sub-plan 02) raise `422` when a `requires_secret` type (`SERVER_TYPE_SPECS`)
      would be saved with no/empty secret (tri-state aware); the `/ui` form (sub-plan 04) softens that
      to an inline error WITHOUT writing or rebuilding. Webhook exempt. JSON API + htmx both enforced.
- [ ] `POST /auth/password` failure path renders `login.html` (no change-password template exists in
      sub-plan 01). Once the settings/account page lands, switch the wrong-current-password error to
      render that template instead. → phase3-01 Task 5; flagged in task review

### Deferred UI polish (sub-plan 04)

- [ ] Server **Test** button posts to the JSON `POST /api/servers/{id}/test` and shows raw JSON; a
      prettier HTML twin (`/ui/servers/{id}/test`) is deferred. → phase3-04 dashboard plan
- [ ] Dashboard/events **live-refresh** polish (poll `/api/status`, htmx SSE extension) — baseline
      ships server-rendered status + a plain `EventSource` feed. → phase3-04 dashboard plan
- [ ] Webhook **edit** form omits `webhook_method`/`webhook_headers_json`/`webhook_body_template`
      (they're set at create but immutable via the UI afterward); add them to the server detail/edit
      form. The `/ui` update handler already accepts them. → phase3-04 Task 2; flagged in task review
- [ ] `library_id` discovery dropdowns (needs a `ServerAdapter.list_libraries()` on the frozen ABC);
      the UI ships **free-text** `library_id` for now. → phase3 README decision 3

### Cosmetic / low-priority (Phase 3 review carry-overs)

- [ ] `ui_update_server` success renders the `_error.html` partial ("Saved." inside a
      `role="alert"` element) — a screen reader announces a success as an alert. Give it a
      `role="status"` `_ok.html` partial (or re-render the updated server partial). → phase3-04 Task 3
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

## Phase 4 — observability & image

- [ ] Verify `mediascanmonitor/migrations/` (incl. `script.py.mako` + `versions/*.py`) ships in
      the wheel/image; add a Hatchling `[tool.hatch.build.targets.wheel.force-include]` if its
      default drops them. → contract §2 (covered by the image smoke test)
- [ ] inotify resilience runbook: privileged `IN_Q_OVERFLOW` / `ENOSPC` reproduction (lower
      `fs.inotify.max_queued_events` / `max_user_watches` in a throwaway container) + a
      real-NAS bulk-import smoke. → 04 self-review

## During implementation (heads-up, not deferred work)

- [ ] Alembic `compare_metadata` may flag spurious enum / SQLite type-affinity diffs on first
      build — add a `compare_type` / `include_object` filter in `migrations/env.py` if so. → 01 Task 3

## Ongoing

- [ ] Keep `_redact_secrets` `SENSITIVE_KEYS` current as new sensitive log fields appear. → 06 Task 1
- [ ] `dev.db` (Alembic autogenerate scratch file) — gitignore it if it becomes a nuisance.
