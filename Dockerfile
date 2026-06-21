# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# media-scan-monitor — multi-stage Python image
#
# Stage 1 (builder): installs the app wheel + all runtime deps via uv.
# Stage 2 (runtime): lean python:3.14-slim, non-root user, /config volume.
#
# Pins:
#   python:3.14-slim  — justified by requires-python = ">=3.14" (pyproject.toml)
#                       and CI python-version: "3.14" (.github/workflows/ci.yml).
#                       Verify the current tag before building (Rule 1).
#   uv 0.11.21        — pinned to the same version CI installs via setup-uv
#                       (ci.yml `version: "0.11.21"`).  Keep in sync.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# Bring in the uv binary.  The tag MUST match ci.yml `setup-uv` `version:`.
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

WORKDIR /app

# UV_PYTHON_DOWNLOADS=0  — use the base-image interpreter; do not fetch Python.
# UV_LINK_MODE=copy      — physically copy files (not hard-links) so that
#                          COPY --from in the runtime stage works correctly.
# UV_COMPILE_BYTECODE=1  — compile .pyc files at install time for faster startup.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Copy only the files uv/hatchling need to resolve the lockfile and build
# the wheel.  README.md and LICENSE are referenced by pyproject.toml
# ([project] readme / license); the build fails without them.  The
# .dockerignore ensures nothing else leaks in from the repo root.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY mediascanmonitor/ ./mediascanmonitor/

# Install the app wheel + all runtime dependencies.
#
# --locked        : assert uv.lock is in sync with pyproject.toml (same gate as CI).
# --no-editable   : build and install the real wheel so migrations/, templates/,
#                   static/, and py.typed ship *inside* the package directory —
#                   not as a live symlink to the source tree.
#
# `dev` is a PEP-621 *extra* (not a dependency group); uv excludes it by
# default.  --no-dev would be a harmless no-op and is intentionally omitted.
RUN uv sync --locked --no-editable

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim

# Install tzdata so structured logs can use a correct local timezone.
# Set TZ at runtime (e.g. -e TZ=UTC or in compose).  Clean apt lists in
# the same RUN layer to avoid a separate cache layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root group and user with fixed UID/GID 1000.  The fixed UID
# lets host bind-mount ownership be set predictably with chown 1000:1000
# (the Synology bind-mount failure mode; see README § /config ownership).
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin app

# Create the /config directory and hand it to the app user.  The app writes
# app.db and secret.key here at startup (db/session.py mkdir + _load_key);
# it must be writable by UID 1000 or startup fails with a permission error
# (not a logic error — the app does not fall back gracefully).
RUN mkdir -p /config && chown app:app /config

# Copy the resolved virtual environment from the builder.
# uv creates .venv in WORKDIR (/app), so the venv lives at /app/.venv.
COPY --from=builder /app/.venv /app/.venv

# Put the venv on PATH and set MSM defaults.  All values are overridable
# at container runtime (docker run -e VAR=value or in compose).
#
# MSM_PASSWORD / MSM_PASSWORD_FILE are intentionally absent: they are
# optional first-run secrets and must never have a baked-in default.
ENV PATH="/app/.venv/bin:$PATH" \
    MSM_HOST="0.0.0.0" \
    MSM_PORT="8080" \
    MSM_DB_PATH="/config/app.db" \
    MSM_SECRET_KEY_FILE="/config/secret.key"

EXPOSE 8080

# Declare /config as a volume so a named volume (no host bind-mount)
# inherits correct ownership from the image's mkdir + chown above.
VOLUME /config

# python:3.14-slim ships no curl or wget.  Use the in-image Python.
# /health is unauthenticated by design (web/api/system.py) — no session
# cookie is needed for the probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"]

# Drop privileges.  msm run is PID 1; uvicorn.Server.serve installs its
# own SIGINT/SIGTERM handlers, and the app is single-process (no workers,
# no zombie reaping), so tini/--init is optional.  Add `init: true` in
# compose for belt-and-suspenders signal handling at zero cost.
USER app

ENTRYPOINT ["msm", "run"]
