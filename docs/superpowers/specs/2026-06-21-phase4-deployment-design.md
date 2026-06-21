# Phase 4 — Deployment & Release Readiness (design spec)

**Date:** 2026-06-21
**Branch:** `app-v2`
**Status:** approved design → input to the Phase 4 implementation plan set

## Goal

Turn the Python app on `app-v2` into a shippable, self-migrating, non-root Docker image with
a real compose stack and a CI gate that proves the image boots and migrates — replacing every
remaining Alpine/Bash deployment artifact. **No application code changes**; this is packaging,
CI, and docs.

## Scope

**In scope (this phase):** Dockerfile rewrite, `.dockerignore`, migrations-in-wheel,
`docker-compose.yml` rewrite (+ opt-in watch-limit sidecar), `docker-build.yml` rewrite
(rename + path filters + image smoke test), README rewrite, and the image-name rename to
`media-scan-monitor`.

**Out of scope — demoted to a "Phase 4b — observability polish" follow-up block:** Prometheus
`/metrics`, dashboard widgets (watch count, per-server health/latency), extension presets, and
optional auth hardening. These were bundled into Phase 4 by `docs/PLAN.md`; they are deliberately
deferred so the deployment half — the actual "viable replacement" gate — ships first.

This phase does **not** merge `app-v2` to `main`. Per project rule, `main` keeps the legacy
`plex_monitor.sh` until the rewrite is a proven, viable replacement; Phase 4 lands on `app-v2`.

## Locked decisions

1. **Phase split:** deployment-readiness only; observability polish deferred to Phase 4b.
2. **Rename mechanics:** decouple the published image name — set `IMAGE_NAME: media-scan-monitor`
   explicitly in `docker-build.yml` so the image publishes as
   `ghcr.io/<owner>/media-scan-monitor` regardless of the repo dir name. The GitHub-repo and
   local-dir rename is a **documented manual cutover step**, not done in this phase (it would
   churn cwd + remote URL mid-branch).
3. **Watch-limit raising:** compose ships an **opt-in, profile-gated, privileged one-shot init
   sidecar** that sets `fs.inotify.max_user_watches`, *and* the README documents the host-level
   `sysctl.d`/boot-task route as the default.
4. **Image smoke test asserts:** boot + non-root + `GET /health` ok + Alembic migrations create
   `app.db` at head on first boot.

## Runtime contract the image/compose must match

Verified against `mediascanmonitor/cli.py`:

| Concern | Value | Source |
| --- | --- | --- |
| Entrypoint | `msm run` (console script) | `[project.scripts]` |
| Listen host | `0.0.0.0` | `MSM_HOST` (default) |
| Listen port | `8080` | `MSM_PORT` (default) |
| DB path | `/config/app.db` | `MSM_DB_PATH` (default) |
| Secret key file | `/config/secret.key` | `MSM_SECRET_KEY_FILE` (default) |
| Secret key (inline) | — | `MSM_SECRET_KEY` (optional) |
| First-run password | — | `MSM_PASSWORD` / `MSM_PASSWORD_FILE` |
| Liveness | `GET /health` (unauth, returns `{"status":"ok"}`) | `web/api/system.py` |

## Components

### A. `Dockerfile` (multi-stage, `python:3.14-slim`)

Replaces the Alpine/Bash Dockerfile (`COPY plex_monitor.sh`).

- **builder stage:** copy the `uv` binary from the pinned `ghcr.io/astral-sh/uv` image; run
  `uv sync --locked --no-editable --no-dev` to build `/app/.venv`. `--no-editable` installs the
  built wheel, so the Alembic migrations must be *inside* the wheel (component C).
- **runtime stage:** `python:3.14-slim` + `tzdata`; create a non-root `app` user (rule 5); copy
  the venv + app from builder; `ENV PATH=/app/.venv/bin:$PATH` and the `MSM_*` defaults;
  `EXPOSE 8080`; `VOLUME /config`; `HEALTHCHECK` hitting `/health`; `USER app`;
  `ENTRYPOINT ["msm", "run"]`.
- **Version pins (rule 1):** the `python:3.14-slim` tag, the `ghcr.io/astral-sh/uv` image tag,
  and any apt package pins are verified current-stable **at execution time**, never pinned from
  memory. The plan flags each as a verify-then-pin step.

### B. `.dockerignore` (new)

Keep the build context small and reproducible: exclude `.git`, `tests`, `docs`, `.venv`,
`__pycache__`/caches, `*.db`, `.superpowers`, and other dev scratch.

### C. `pyproject.toml` — migrations in the wheel

Add `[tool.hatch.build.targets.wheel.force-include]` (or `artifacts`) so `mediascanmonitor/
migrations/` ships in the wheel: `env.py`, `script.py.mako`, and `versions/*.py`. The non-`.py`
`script.py.mako` is the file Hatchling drops by default; that omission would make a fresh
container fail to migrate.

### D. `docker-compose.yml` rewrite

- `media-scan-monitor` service: the ghcr image, `restart: unless-stopped`, `8080:8080`,
  `./config:/config`, a **local** media bind-mount example (`/path/to/media:/data/media:ro`,
  placeholder path) with the inotify-needs-local-storage warning, `TZ`, `MSM_PASSWORD_FILE`,
  and a healthcheck.
- **Opt-in init sidecar** under `profiles: ["init-watches"]`: a one-shot privileged container
  that runs `sysctl -w fs.inotify.max_user_watches=${MSM_INOTIFY_WATCHES:-131072}`. Loudly
  commented as **privileged + host-global** (writes a kernel value affecting the whole host);
  off by default, enabled only with `--profile init-watches`.

### E. `.github/workflows/docker-build.yml` rewrite

- **Path filters** → `mediascanmonitor/**`, `uv.lock`, `pyproject.toml`, `Dockerfile`,
  `.dockerignore`, and the workflow file. Drop the `plex_monitor.sh` trigger.
- **Image name** → `IMAGE_NAME: media-scan-monitor` (explicit; decoupled from
  `${{ github.repository }}`). Published as `ghcr.io/<owner>/media-scan-monitor`.
- **Smoke-test job** (gates the push): build the image (single-arch amd64 for speed), run the
  container against a throwaway `/config`, and assert: (1) the process runs as non-root,
  (2) `GET /health` returns ok, (3) the bundled Alembic migrations created `app.db` at head on
  first boot. This is what closes the migrations-in-image follow-up at runtime.

### F. `README.md` full rewrite

For the Python app: what it is + architecture summary, compose quickstart, config-via-UI,
first-run password (`MSM_PASSWORD`/`MSM_PASSWORD_FILE`), volumes (config + the local-bind /
inotify warning), the watch-limit setup (host `sysctl.d` default **and** the sidecar profile),
an env/port reference table, and a "no events ever seen" troubleshooting section (bind mount is
local? watch limit sufficient?). Keep it in sync with `CLAUDE.md` (rule 9).

## Testing strategy

- **CI image smoke test** (primary gate): boot + non-root + `/health` + migration-to-head, on
  the built image (component E).
- **Wheel-contents check** (cheap, deterministic, runs before the slower smoke test): a CI step
  that builds the wheel and asserts `migrations/versions/*.py` + `script.py.mako` are present —
  fails fast at build time. Together with the smoke test this closes the migrations-in-image
  follow-up from both the static and runtime sides.
- **Compose validation:** `docker compose config` as a lint; no separate compose unit test.
- The privileged `IN_Q_OVERFLOW`/`ENOSPC` reproduction + real-NAS bulk-import **runbook stays
  deferred** (can't run in CI); the README carries the user-facing troubleshooting subset.

No application unit/integration tests change — this phase touches packaging, CI, and docs only.

## Likely sub-plan decomposition (for the writing-plans step)

| # | Sub-plan | Deliverable |
| --- | --- | --- |
| `00` | Deployment contract | Frozen vocab: paths/env table, image name, volumes, gate, sidecar profile name, smoke-test assertions |
| `01` | Image | `Dockerfile` + `.dockerignore` + wheel force-include + wheel-contents check |
| `02` | Compose | `docker-compose.yml` + opt-in `init-watches` sidecar |
| `03` | CI | `docker-build.yml` rename + path filters + image smoke-test job |
| `04` | Docs | `README.md` rewrite + `docs/FOLLOWUPS.md` reshuffle (mark migrations-in-wheel done; create the Phase 4b observability-polish block) |

## Follow-ups this phase resolves / creates

- **Resolves:** "Verify `migrations/` ships in the wheel/image" (FOLLOWUPS Phase 4) — closed by
  the wheel-contents check + smoke test.
- **Creates / keeps deferred:** Phase 4b observability block (metrics, widgets, presets, auth
  hardening); the inotify resilience runbook (privileged repro + real-NAS smoke); the
  GitHub-repo/local-dir rename as a documented cutover step.
