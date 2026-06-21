# Phase 4 — Frozen Deployment Contract

> **For agentic workers:** this is the frozen deployment vocabulary for Phase 4. Read it before any
> Phase 4 sub-plan. Each value below is consumed by more than one sub-plan; a drift between two
> consumers (e.g. the `EXPOSE` port vs the compose port mapping vs the healthcheck vs the smoke
> probe) silently breaks the image. A sub-plan may not change a value here without updating this
> file **and** every consumer. Anything not nailed down here (exact `RUN` layering, comment wording,
> README prose) is the owning sub-plan's choice.

This phase adds packaging/CI/docs around the **unchanged** Phase 1–3 app. It introduces **no**
application code. The only new Python is one test (`tests/build/test_wheel_contents.py`).

---

## A. Image identity, registry, tags (owned by sub-plan 03; consumed by 02, 04)

- **Image name (decoupled from repo dir):** set in the workflow as
  `IMAGE_NAME: ${{ github.repository_owner }}/media-scan-monitor`. A bare `media-scan-monitor`
  (no owner) is an invalid GHCR path — the owner namespace is required.
- **Full reference:** `ghcr.io/<owner>/media-scan-monitor` (GHCR lowercases the owner; the legacy
  image was `ghcr.io/doctorkomodo/...`).
- **Tag strategy** (`docker/metadata-action`):
  - `type=ref,event=branch` → publishes the branch name as a tag, so an `app-v2` push yields the
    **`app-v2`** tag.
  - `type=raw,value=latest,enable={{is_default_branch}}` → `latest` only on the default branch
    (`main`); it stays **unset** until cutover.
  - keep `type=semver` patterns for future `v*` tags.
- **Publish gating:** only on real pushes (`github.event_name != 'pull_request'`). PRs build +
  smoke-test but do not push.

## B. Runtime contract — the image/compose MUST match these (owned by 01; consumed by 02, 03, 04)

Verified against `mediascanmonitor/cli.py`, `mediascanmonitor/db/session.py`,
`mediascanmonitor/web/auth.py`, `mediascanmonitor/web/api/system.py`:

| Concern | Value | Env override | Source of truth |
| --- | --- | --- | --- |
| Entrypoint | `msm run` | — | `[project.scripts]` (`cli.py`) |
| Listen host | `0.0.0.0` | `MSM_HOST` | `cli.py` |
| **Listen port** | **`8080`** | `MSM_PORT` | `cli.py` |
| DB path | `/config/app.db` | `MSM_DB_PATH` | `cli.py` / `db/session.py` |
| Secret key file | `/config/secret.key` | `MSM_SECRET_KEY_FILE` | `cli.py` |
| Secret key (inline) | — | `MSM_SECRET_KEY` | `cli.py` |
| First-run password | — | `MSM_PASSWORD_FILE` (stripped) > `MSM_PASSWORD` | `web/auth.py` |
| Liveness | `GET /health` → `{"status":"ok"}`, **unauth** | — | `web/api/system.py` |
| Timezone | `tzdata` installed; set via `TZ` | `TZ` | log timestamps |

`8080` is the single most-copied value — it appears in the Dockerfile `EXPOSE`, the `HEALTHCHECK`
URL, the compose `ports`, and the smoke-test probe. They must all read `8080`.

## C. Non-root user (owned by 01; consumed by 02 ownership note, 03 smoke assertion, 04 docs)

- The runtime stage creates a non-root user **`app` with fixed UID **`1000`** and GID **`1000`****
  and runs `USER app`.
- This fixed UID is the value the bind-mount-ownership documentation (04) and the smoke-test
  `chown` (03) reference. Do not use a random/system-assigned UID.

## D. `/config` volume + writability (owned by 01 `VOLUME`; consumed by 02, 03, 04)

- `/config` holds `app.db` + `secret.key`; the app **writes** both at startup
  (`db/session.py` `create_db_engine` does `parent.mkdir`; `cli.py` `_load_key` writes the key).
- Under `USER app` (UID 1000), `/config` must be **writable by UID 1000** or startup fails on a
  permission error (not a logic error). Consequences:
  - **Smoke test (03):** the throwaway `/config` it mounts must be `chown`ed to `1000:1000` (or use
    a fresh named volume / a dir created writable) before `docker run`.
  - **README (04):** document that a host bind mount (`./config`) must be owned by UID 1000
    (`chown -R 1000:1000 ./config`) — the classic Synology bind-mount failure.
- `Dockerfile` declares `VOLUME /config` and the image creates `/config` owned by `app:app` so a
  named volume (no host bind) inherits correct ownership automatically.

## E. Build recipe (owned by 01; the uv pin is also a 03 concern)

- **Multi-stage**, base `python:3.14-slim` (verify the exact current tag at execution per rule 1;
  consider a digest pin). `requires-python = ">=3.14"` and CI's `python-version: "3.14"` fix the
  major.minor.
- **builder stage:** bring the `uv` binary in via
  `COPY --from=ghcr.io/astral-sh/uv:<tag> /uv /uvx /bin/`. The `<tag>` **must equal CI's pinned uv
  version** — read it from `.github/workflows/ci.yml` (`setup-uv` `version:`; currently `0.11.21`),
  not "latest" and not from memory (rule 1 + CLAUDE.md "image, CI, dev box resolve identically").
- **Install:** `uv sync --locked --no-editable` (installs the built wheel → exercises the in-wheel
  asset path). `dev` is a PEP-621 *extra*, excluded by default — do **not** pass `--extra dev`, and
  `--no-dev` is an unnecessary no-op (omit it).
- **runtime stage:** copy the resolved environment from the builder; `apt-get install tzdata`
  (clean apt lists in the same layer); create the `app` user (§C); `ENV` the `MSM_*` defaults +
  put the venv on `PATH`; `EXPOSE 8080`; `VOLUME /config`; `HEALTHCHECK` (§F); `USER app`;
  `ENTRYPOINT ["msm", "run"]` (no args → serves web + engine).

## F. HEALTHCHECK (owned by 01; mirrored by 03's probe)

`python:3.14-slim` has **no** `curl`/`wget`. Use in-image Python:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"]
```

(`/health` is unauthenticated by design — see `web/api/system.py`.)

## G. Wheel assets that MUST ship — regression-guard list (owned by 01 test; referenced by spec §C)

Hatchling already ships these by default (`packages = ["mediascanmonitor"]` includes all files
under the package — empirically verified by building the wheel). The guard **asserts they stay**:

- `mediascanmonitor/migrations/env.py`
- `mediascanmonitor/migrations/script.py.mako`  ← the non-`.py` file, the one most at risk
- `mediascanmonitor/migrations/versions/0001_initial.py` (at least one `versions/*.py`)
- at least one `mediascanmonitor/web/templates/*.html`
- `mediascanmonitor/web/static/app.css` and `mediascanmonitor/web/static/htmx.min.js`
- `mediascanmonitor/py.typed`

Do **not** add a `force-include`/`artifacts` directive (it is unnecessary and risks duplicate
inclusion). The guard is a *test*, not a build-config change.

## H. Init-watches sidecar (owned by 02; documented by 04)

- A second compose service named **`init-watches`**, gated behind `profiles: ["init-watches"]`
  (off by default; enabled only with `docker compose --profile init-watches up`).
- **Privileged, host-global:** runs `sysctl -w fs.inotify.max_user_watches=${MSM_INOTIFY_WATCHES:-131072}`
  once and exits. Loudly commented as privileged + writing a host-wide kernel value.
- The app **never** writes the sysctl itself; this sidecar is the only in-stack writer, and it is
  opt-in. The default path is the host-level `sysctl.d` route documented in the README.

## I. Smoke-test assertions (owned by 03; depends on 01's image)

Against the built amd64 image, run a container with a **writable** `/config` (§D) and assert all of:

1. **Non-root:** `docker exec <c> id -u` returns `1000` (not `0`).
2. **Liveness:** `GET http://localhost:<mapped>/health` returns HTTP 200 with `{"status":"ok"}`.
3. **Migrated to head:** `/config/app.db` exists and its `alembic_version.version_num` equals the
   repo's current Alembic head (compute the head from the repo, compare — do not hardcode the
   revision id).

## J. Files this phase creates / modifies (whole-phase map)

| File | Sub-plan | Action |
| --- | --- | --- |
| `Dockerfile` | 01 | rewrite (was Alpine/Bash) |
| `.dockerignore` | 01 | create |
| `tests/build/test_wheel_contents.py` | 01 | create |
| `tests/build/__init__.py` | 01 | create (if the suite needs it) |
| `docker-compose.yml` | 02 | rewrite (was Plex/Bash) |
| `.github/workflows/docker-build.yml` | 03 | rewrite |
| `.github/workflows/ci.yml` | 03 | add a wheel-contents job (or fold the guard into `pytest`) |
| `README.md` | 04 | full rewrite |
| `docs/FOLLOWUPS.md` | 04 | reshuffle (resolve migrations-in-wheel; add Phase 4b block) |
| `pyproject.toml` | — | **no change** (force-include is NOT added; §G) |
| `mediascanmonitor/**` | — | **no change** (no app code in this phase) |

## K. Out of scope (Phase 4b — do not build)

Prometheus `/metrics`, dashboard widgets, extension presets, auth hardening, the privileged
`IN_Q_OVERFLOW`/`ENOSPC` runbook, and the actual GitHub-repo/local-dir rename. 04 only files these
as follow-ups; it does not implement them.
