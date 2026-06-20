# Deferred follow-ups (index)

Cross-phase items not done in the phase that surfaced them. Detail + rationale live in the
linked plan/contract section (single source of truth); this is just the scannable list.
Add a pointer here when you defer something to a later phase; **remove the item when it's done.**

## Phase 3 — web UI

- [ ] `Engine.rebuild()` full `blocked`↔`running` inotify-gate recovery — Phase 1 consults the
      gate only in `start()`. → 06 design-decision #7; contract §10

## Later — targeted scans for the non-Plex backends

- [ ] Per-folder targeted scans for Emby/Jellyfin/Audiobookshelf (Phase 2 ships library-refresh
      only, by deliberate choice). Verified endpoints: Emby/Jellyfin `POST /Library/Media/Updated`
      (`{Updates:[{Path,UpdateType}]}`); Audiobookshelf `POST /api/watcher/update`
      (`{libraryId,path,type}`, ABS ≥2.9.0 — note reliability bug advplyr/audiobookshelf#3018).
      Adding it = extend each adapter's `supported_scan_modes` with `targeted` + a path-targeted
      `trigger()` branch; the UI then offers the mode. → phase2-README convention 2

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
