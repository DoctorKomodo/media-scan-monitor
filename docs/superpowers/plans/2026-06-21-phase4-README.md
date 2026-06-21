# Phase 4 — Detailed Implementation Plans (index)

Phase 4 of `media-scan-monitor` — **"Deployment & Release Readiness"** (the deployment half of
[`docs/PLAN.md`](../../PLAN.md) §"Phase 4"; the observability half is deferred to Phase 4b). It
turns the Python app on `app-v2` into a shippable, self-migrating, **non-root** Docker image with
a real compose stack and a CI gate that proves the image boots and migrates — replacing every
remaining Alpine/Bash deployment artifact and publishing a pullable pre-release image for
real-hardware validation.

The approved design this expands is
[`docs/superpowers/specs/2026-06-21-phase4-deployment-design.md`](../specs/2026-06-21-phase4-deployment-design.md)
(Opus-reviewed; the wheel was built during review, disproving a force-include premise — Hatchling
already ships every runtime asset by default).

**No application code changes.** This phase touches packaging, CI, and docs only. The single new
*test* is a wheel-contents regression guard (`tests/build/test_wheel_contents.py`).

Each sub-plan is written for an engineer with zero codebase context: bite-sized tasks, exact file
paths, complete file contents in every step, exact commands with expected output. Because this is
packaging (not red-green app code), tasks are "author the artifact → validate it → commit"; the
one genuine pytest lives in sub-plan 01.

## Frozen deployment contract

Phase 4 introduces shared deployment vocabulary consumed across all four sub-plans — the image
name/tags, the listen port, the non-root UID, the `/config` paths, the wheel-asset list, the
sidecar profile name, and the smoke-test assertions. A drift between any two (e.g. `EXPOSE` vs the
compose port vs the healthcheck vs the smoke probe) silently breaks the image. All of it is frozen
up front in:

> [`…-phase4-00-deployment-contract.md`](2026-06-21-phase4-00-deployment-contract.md)

Read it before any sub-plan. A sub-plan may not change a frozen value without updating the contract
**and** every consumer.

## Documents

| # | File | Builds |
|---|------|--------|
| 00 | [deployment contract](2026-06-21-phase4-00-deployment-contract.md) | Frozen vocab: image ref + tag strategy, runtime env/path table, non-root UID, `/config` ownership rule, build recipe (uv pin), wheel-asset list, sidecar profile, smoke-test assertions. |
| 01 | [image](2026-06-21-phase4-01-image.md) | `Dockerfile` (multi-stage `python:3.14-slim`, non-root, Python `HEALTHCHECK`, uv pinned to CI) + `.dockerignore` + `tests/build/test_wheel_contents.py` (the regression guard: migrations + templates + static + `py.typed`). |
| 02 | [compose](2026-06-21-phase4-02-compose.md) | `docker-compose.yml` rewrite (config volume + local media bind warning, `init: true`, bind-mount ownership note) + the opt-in `init-watches` privileged watch-limit sidecar. |
| 03 | [CI](2026-06-21-phase4-03-ci.md) | `.github/workflows/docker-build.yml` rewrite: owner-namespaced `IMAGE_NAME`, path filters, `app-v2` trigger + `app-v2` publish tag (`latest` stays default-branch-gated), amd64 smoke-test job (boot + non-root + `/health` + migrate-to-head against a chowned `/config`), preserved multi-arch publish + wheel-contents job. |
| 04 | [docs](2026-06-21-phase4-04-docs.md) | `README.md` full rewrite (quickstart, env/port table, `/config` ownership, `secret.key` persistence, watch-limit setup, no-events troubleshooting) + `docs/FOLLOWUPS.md` reshuffle (resolve migrations-in-wheel; create the Phase 4b observability block). |

## Dependency graph

```
Phase 1–3 (engine, repo, adapters, web app)  ── unchanged code
        │
        ▼
   00 contract (frozen vocab)
        │
        ▼
   01 image ──┬─> 03 CI (smoke test needs the Dockerfile) ──┐
              │                                              ├─> (image publishes)
   02 compose ┴──────────────────────────────────────────┐  │
        │                                                 │  │
        └────────────────> 04 docs (README documents compose + image + CI) <┘
```

- **01 image** is the foundation: the Dockerfile defines the port, the non-root UID, the
  healthcheck, and the asset surface every other doc references.
- **03 CI** consumes 01 (the smoke-test job builds and runs the Dockerfile) — sequence it after 01.
- **02 compose** is independent of 03 and may be built in parallel with it; 03's
  `docker compose config` lint reads the file 02 authors (note the cross-task touch).
- **04 docs** documents the compose stack (02), the image env/ports (00/01), and the watch-limit
  sidecar (02) — build it last.

## Canonical execution order

1. **00 contract** — freeze the shared values first.
2. **01 image** — Dockerfile + `.dockerignore` + wheel-contents guard.
3. **02 compose** / **03 CI** — independent of each other (03 needs 01; 02 needs only 00).
4. **04 docs** — README + FOLLOWUPS on top of the rest.

## Decisions locked for this phase (asked + answered at planning time)

1. **Scope:** deployment-readiness only; observability polish (Prometheus `/metrics`, dashboard
   widgets, extension presets, auth hardening) is demoted to a **Phase 4b** follow-up block. Do not
   build it here.
2. **Rename:** decouple the published image name — `IMAGE_NAME: ${{ github.repository_owner }}/media-scan-monitor`
   (owner-namespaced; a bare `media-scan-monitor` is invalid for GHCR). The GitHub-repo/local-dir
   rename is a **documented manual cutover step**, not done this phase.
3. **Watch limit:** compose ships an **opt-in, profile-gated, privileged** `init-watches` sidecar
   that sets `fs.inotify.max_user_watches`, *and* the README documents the host `sysctl.d` route as
   the default.
4. **Smoke test asserts:** boot + non-root + `GET /health` ok + Alembic migrations create `app.db`
   at head — against a `/config` made writable by the image's non-root UID.
5. **Publish (pre-cutover):** on `app-v2` pushes, build → smoke → publish **multi-arch** under the
   non-`latest` `app-v2` tag, so the image is pullable for real-hardware validation. `latest` stays
   gated on the default branch.
6. **No `main` merge this phase.** `main` keeps the legacy `plex_monitor.sh` until the rewrite is a
   proven viable replacement. Phase 4 lands on `app-v2`.

## Project rules that bind every task (from [`CLAUDE.md`](../../../CLAUDE.md))

- **Rule 1 — versions current-stable, verified at add-time, never from memory.** This governs the
  Python interpreter and **base images** too. The `python:3.14-slim` tag, the `ghcr.io/astral-sh/uv`
  tag, and any apt pins are verified at execution; the uv tag must **match CI's pinned version**
  (read it from `.github/workflows/ci.yml` — currently `0.11.21`), not "latest".
- **Rule 5 — security / non-root.** The container runs as a non-root user; never log secrets.
- **Rule 7 — migrations.** Schema changes go through Alembic; the image migrates to `head` on boot
  (already implemented in `db/session.py` `init_db`). This phase only ensures migrations *ship*.
- **Rule 9 — docs in sync.** Keep `README.md` and `CLAUDE.md` aligned with reality.
