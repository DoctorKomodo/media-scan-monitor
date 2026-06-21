# Phase 4 — Deployment & Release Readiness (design spec)

**Date:** 2026-06-21
**Branch:** `app-v2`
**Status:** approved design (Opus-reviewed, fixes folded in) → input to the Phase 4 plan set

## Goal

Turn the Python app on `app-v2` into a shippable, self-migrating, non-root Docker image with
a real compose stack and a CI gate that proves the image boots and migrates — replacing every
remaining Alpine/Bash deployment artifact, and publishing a pullable pre-release image so the
rewrite can be validated on real hardware. **No application code changes**; this is packaging,
CI, and docs.

## Scope

**In scope (this phase):** Dockerfile rewrite, `.dockerignore`, a wheel-contents regression
guard, `docker-compose.yml` rewrite (+ opt-in watch-limit sidecar), `docker-build.yml` rewrite
(rename + path filters + `app-v2` trigger/publish + image smoke test), README rewrite, and the
image-name rename to `media-scan-monitor`.

**Out of scope — demoted to a "Phase 4b — observability polish" follow-up block:** Prometheus
`/metrics`, dashboard widgets (watch count, per-server health/latency), extension presets, and
optional auth hardening. These were bundled into Phase 4 by `docs/PLAN.md`; they are deliberately
deferred so the deployment half — the actual "viable replacement" gate — ships first.

This phase does **not** merge `app-v2` to `main`. Per project rule, `main` keeps the legacy
`plex_monitor.sh` until the rewrite is a proven, viable replacement; Phase 4 lands on `app-v2`,
publishing a non-`latest` pre-release tag.

## Locked decisions

1. **Phase split:** deployment-readiness only; observability polish deferred to Phase 4b.
2. **Rename mechanics:** decouple the published image name. Set it explicitly in
   `docker-build.yml` as `IMAGE_NAME: ${{ github.repository_owner }}/media-scan-monitor` so the
   image publishes as `ghcr.io/<owner>/media-scan-monitor` regardless of the repo dir name. The
   bare `media-scan-monitor` (no owner) is invalid for GHCR — the owner namespace is required.
   The GitHub-repo and local-dir rename is a **documented manual cutover step**, not done in this
   phase (it would churn cwd + remote URL mid-branch).
3. **Watch-limit raising:** compose ships an **opt-in, profile-gated, privileged one-shot init
   sidecar** that sets `fs.inotify.max_user_watches`, *and* the README documents the host-level
   `sysctl.d`/boot-task route as the default.
4. **Image smoke test asserts:** boot + non-root + `GET /health` ok + Alembic migrations create
   `app.db` at head on first boot — against a `/config` made writable by the image's non-root UID.
5. **Publish strategy (pre-cutover):** on `app-v2` pushes the workflow builds, smoke-tests, then
   publishes a **multi-arch** image to `ghcr.io/<owner>/media-scan-monitor` under a non-`latest`
   tag (`app-v2`). `latest` remains gated on the default branch and stays unset until cutover.
   This makes the image pullable for real-hardware "viable replacement" validation.

## Runtime contract the image/compose must match

Verified against `mediascanmonitor/cli.py` and `mediascanmonitor/web/auth.py`:

| Concern | Value | Source |
| --- | --- | --- |
| Entrypoint | `msm run` (console script) | `[project.scripts]` |
| Listen host | `0.0.0.0` | `MSM_HOST` (default) |
| Listen port | `8080` | `MSM_PORT` (default) |
| DB path | `/config/app.db` | `MSM_DB_PATH` (default) |
| Secret key file | `/config/secret.key` | `MSM_SECRET_KEY_FILE` (default) |
| Secret key (inline) | — | `MSM_SECRET_KEY` (optional) |
| First-run password | — | `MSM_PASSWORD_FILE` (stripped) > `MSM_PASSWORD` (`web/auth.py`) |
| Liveness | `GET /health` (unauth, `{"status":"ok"}`) | `web/api/system.py` |

The app writes `app.db` and `secret.key` under `/config` at startup (`db/session.py` mkdir +
create; `cli.py` `_load_key`). Under `USER app` (non-root) those writes require `/config` to be
owned/writable by the container UID — see G-notes in Components D/E/F.

## Components

### A. `Dockerfile` (multi-stage, `python:3.14-slim`)

Replaces the Alpine/Bash Dockerfile (`COPY plex_monitor.sh`).

- **builder stage:** copy the `uv` binary from `ghcr.io/astral-sh/uv:<tag>` (pin the **same uv
  version CI uses** — read it from `.github/workflows/ci.yml`, do not hardcode from memory); run
  `uv sync --locked --no-editable` to build `/app/.venv`. `--no-editable` installs the built
  wheel (exercising the in-wheel-assets path). Note: `dev` is a PEP-621 *extra*, not a dependency
  group, so `uv sync` already excludes it by default — `--no-dev` would be a harmless no-op and
  is omitted.
- **runtime stage:** `python:3.14-slim` + `tzdata`; create a non-root `app` user (rule 5); copy
  the venv + app from builder; `ENV PATH=/app/.venv/bin:$PATH` and the `MSM_*` defaults;
  `EXPOSE 8080`; `VOLUME /config`; `USER app`; `ENTRYPOINT ["msm", "run"]`.
- **HEALTHCHECK:** `slim` ships no `curl`/`wget` — use in-image Python, e.g.
  `CMD ["python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"]`.
- **PID 1 / signals:** `msm run` is PID 1 but `uvicorn.Server.serve` installs its own
  SIGINT/SIGTERM handlers (`web/server.py`), and it's single-process (no workers, no zombie
  reaping). `tini`/`--init` is therefore optional, not required — the plan states this rather
  than assuming it.
- **Version pins (rule 1):** the `python:3.14-slim` tag and the `ghcr.io/astral-sh/uv` tag are
  verified current/CI-matched **at execution time**, never from memory; consider digest pins.

### B. `.dockerignore` (new)

Keep the build context small and reproducible. Exclude `.git`, `tests`, `docs`, `.venv`,
`__pycache__`/caches, `*.db`, `.superpowers`, and other dev scratch — but the build is a wheel
build, so it MUST retain `mediascanmonitor/**`, `pyproject.toml`, `uv.lock`, **`README.md`**, and
**`LICENSE`** (the latter two are referenced by `[project] readme`/`license` and the build fails
without them). `alembic.ini` is **not** needed at runtime (`init_db` builds its Alembic `Config`
programmatically and never reads it), so excluding it is safe — stated so nobody "fixes" a
non-bug by shipping it.

### C. Migrations + assets ship in the wheel (no build-config change needed)

**Empirically verified by the review (wheel built from the current config):** Hatchling's
`[tool.hatch.build.targets.wheel] packages = ["mediascanmonitor"]` already includes *all* files
under the package — `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/*.py`,
`web/templates/*.html`, `web/static/*`, and `py.typed`. **No `force-include`/`artifacts`
directive is added** (it would risk duplicate inclusion and solves a non-problem).

Instead, lock this with a **wheel-contents regression guard** (a fast CI check, see Testing) that
builds the wheel and asserts the non-`.py` assets are present: `migrations/script.py.mako` +
`migrations/versions/*.py` + `web/templates/*.html` + `web/static/*` + `py.typed`. This guards
*all* runtime-essential non-`.py` assets, not just migrations — a missing template would still
pass the boot/`/health`/migrate smoke test while serving a broken UI.

### D. `docker-compose.yml` rewrite

- `media-scan-monitor` service: the ghcr image, `restart: unless-stopped`, `8080:8080`,
  `./config:/config`, a **local** media bind-mount example (`/path/to/media:/data/media:ro`,
  placeholder path) with the inotify-needs-local-storage warning, `TZ`, `MSM_PASSWORD_FILE`, a
  healthcheck, and `init: true` (cheap robustness for PID-1).
- **Bind-mount ownership note:** `./config` must be writable by the container's non-root UID, or
  startup fails on a permission error (Synology bind mounts are the exact pain point). Documented
  in-file and expanded in the README.
- **Opt-in init sidecar** under `profiles: ["init-watches"]`: a one-shot privileged container
  that runs `sysctl -w fs.inotify.max_user_watches=${MSM_INOTIFY_WATCHES:-131072}`. Loudly
  commented as **privileged + host-global** (writes a kernel value affecting the whole host); off
  by default, enabled only with `--profile init-watches`.

### E. `.github/workflows/docker-build.yml` rewrite

- **Path filters** → `mediascanmonitor/**`, `uv.lock`, `pyproject.toml`, `Dockerfile`,
  `.dockerignore`, and the workflow file. Drop the `plex_monitor.sh` trigger.
- **Triggers** → add `app-v2` to the `push` branches (currently `main`-only, so nothing builds
  on this branch today). Keep `main` + `v*` tags.
- **Image name** → `IMAGE_NAME: ${{ github.repository_owner }}/media-scan-monitor` (explicit,
  owner-namespaced; decoupled from the repo dir).
- **Tags** → publish the `app-v2` branch tag on `app-v2` pushes; keep `latest` gated on the
  default branch (`is_default_branch`) so it stays unset until cutover.
- **Smoke-test job** (gates the publish): build the image (single-arch **amd64**, `--load` for a
  runnable local image — buildx can't `--load` multi-platform), run it against a throwaway
  `/config` **chowned to the image's non-root UID**, and assert: (1) the process runs as non-root,
  (2) `GET /health` returns ok, (3) the bundled Alembic migrations created `app.db` at head on
  first boot.
- **Multi-arch publish preserved:** after smoke passes, the gated push publishes
  `linux/amd64,linux/arm64` (arm64 matters for NAS targets) — a second buildx invocation sharing
  the gha cache. The amd64-only smoke build must not silently drop arm64 from the publish.

### F. `README.md` full rewrite

For the Python app: what it is + architecture summary, compose quickstart, config-via-UI,
first-run password (`MSM_PASSWORD`/`MSM_PASSWORD_FILE`), volumes (config + the local-bind /
inotify warning), the watch-limit setup (host `sysctl.d` default **and** the sidecar profile), an
env/port reference table, and a "no events ever seen" troubleshooting section. Must also cover:

- **`/config` ownership** — the container runs non-root; chown the bind mount to the container
  UID (or the Synology PUID/PGID story) or startup fails to write `app.db`/`secret.key`.
- **`secret.key` persistence** — server secrets are Fernet-encrypted with `/config/secret.key`;
  losing or regenerating the key makes every stored secret undecryptable. Keep `/config`
  persistent; back up `secret.key`.

Keep it in sync with `CLAUDE.md` (rule 9).

## Testing strategy

- **Wheel-contents regression guard** (fast, deterministic, in `ci.yml` as its own job so it
  gates every PR without a Docker build): build the wheel and assert the non-`.py` runtime assets
  are present (Component C). Runs independently of — and faster than — the image smoke test.
- **CI image smoke test** (primary runtime gate, in `docker-build.yml`): boot + non-root +
  `/health` + migration-to-head, against a writable throwaway `/config` (Component E).
- **Compose validation:** `docker compose config` as a lint in CI; no separate compose unit test.
- The privileged `IN_Q_OVERFLOW`/`ENOSPC` reproduction + real-NAS bulk-import **runbook stays
  deferred** (can't run in CI); the README carries the user-facing troubleshooting subset.

No application unit/integration tests change — this phase touches packaging, CI, and docs only.

## Sub-plan decomposition (for the writing-plans step)

| # | Sub-plan | Deliverable |
| --- | --- | --- |
| `00` | Deployment contract | Frozen vocab: paths/env table, image name (owner-namespaced), volumes + `/config` ownership rule, gate, sidecar profile name, publish/tag strategy, smoke-test assertions |
| `01` | Image | `Dockerfile` (multi-stage, slim, non-root, Python `HEALTHCHECK`, uv pinned to CI) + `.dockerignore` (keeps README/LICENSE/pyproject/uv.lock) + wheel-contents guard in `ci.yml` covering migrations + templates + static + `py.typed` |
| `02` | Compose | `docker-compose.yml` (`init: true`, bind-mount ownership note) + opt-in `init-watches` sidecar |
| `03` | CI | `docker-build.yml`: rename (owner-namespaced `IMAGE_NAME`) + path filters + `app-v2` trigger + `app-v2`-tag publish (latest stays default-branch-gated) + amd64 smoke job (chowned `/config`) + preserved multi-arch publish. Acceptance criteria enumerate C3/C4/G2/G4 so none is lost. Sequenced after `01` (smoke needs the Dockerfile); may split rename/trigger plumbing from the smoke job if it grows. |
| `04` | Docs | `README.md` rewrite (incl. `/config` ownership + `secret.key` persistence) + `docs/FOLLOWUPS.md` reshuffle (mark migrations-in-wheel resolved; create the Phase 4b observability-polish block) |

Ordering: `00 → 01 → {02, 03} → 04` (`02` and `03` independent; `03`'s `docker compose config`
lint touches the file authored in `02`).

## Follow-ups this phase resolves / creates

- **Resolves:** "Verify `migrations/` ships in the wheel/image" (FOLLOWUPS Phase 4) — already
  shipped by Hatchling's defaults; **locked** by the wheel-contents regression guard (now also
  covering templates/static/`py.typed`).
- **Creates / keeps deferred:** Phase 4b observability block (metrics, widgets, presets, auth
  hardening); the inotify resilience runbook (privileged repro + real-NAS smoke); the
  GitHub-repo/local-dir rename as a documented cutover step.
