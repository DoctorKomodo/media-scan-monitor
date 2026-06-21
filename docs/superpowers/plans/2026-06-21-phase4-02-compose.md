# Phase 4 Sub-Plan 02 — Compose (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy Plex/Bash `docker-compose.yml` with a Python-app stack: the
`media-scan-monitor` service (pulls the ghcr image; config volume with ownership note; local
media bind-mount example; `TZ`; `MSM_PASSWORD_FILE` via compose secrets; image healthcheck
is self-reported; `init: true`) plus an opt-in, profile-gated, privileged `init-watches` one-shot
sidecar that raises `fs.inotify.max_user_watches`.

**Architecture:** A single `docker-compose.yml` with two top-level service entries. The primary
`media-scan-monitor` service is always active. The `init-watches` service is gated behind
`profiles: ["init-watches"]` — completely absent from a plain `docker compose up` and only
materialised when the user passes `--profile init-watches`. The sidecar is a one-shot that sets a
host-global kernel value via `sysctl` and exits (`restart: "no"`); it runs before the main service
starts in `--profile` mode because it has no `depends_on`, but Docker starts services in parallel
by default. Add `depends_on: [init-watches]` only if sequencing is needed — compose profiles
don't enforce ordering across profiles; the sidecar's `restart: "no"` means it exits cleanly. Note:
the sidecar doesn't need `depends_on` wiring back to the main service — `sysctl` is effectively
instantaneous and the app's own watch-limit gate (`watcher/watch_limit.py`) retries if the limit
isn't yet raised.

**Tech Stack:** Docker Compose v2 YAML (`services`, `secrets`, `profiles`). No Python, no new
dependencies. Validation: `docker compose config` (lint/resolve). Commit: one task, one file.

## Global Constraints

These are binding (contract §A–§H):

- **Port:** `8080:8080` (contract §B — single most-copied value; the image `EXPOSE`s `8080` and
  the healthcheck uses `8080`).
- **UID:** `1000` (contract §C — the image runs as non-root user `app` with fixed UID/GID 1000;
  the host bind-mount `./config` must be `chown -R 1000:1000 ./config` or startup fails to write
  `app.db`/`secret.key`).
- **Image ref:** `ghcr.io/doctorkomodo/media-scan-monitor:app-v2` (contract §A — GHCR lowercases
  the owner; `doctorkomodo` matches the legacy image's owner namespace; `app-v2` is the branch tag
  published pre-cutover; users on a versioned release would substitute a semver tag or `latest`).
- **Sidecar profile name:** `init-watches` (contract §H).
- **MSM_INOTIFY_WATCHES default:** `131072` (contract §H — recommended ceiling, not kernel-mandated).
- **Secrets:** prefer `MSM_PASSWORD_FILE` wired via compose `secrets:` block (never inline in
  `environment:`); `MSM_PASSWORD` env var is the simpler fallback (rule 5, never log secrets).
- **No `build:` key** in the compose file — Container Manager on Synology tries to build when
  `build:` is present; keep it absent (pull the published image only).
- **Image healthcheck:** the image (`Dockerfile`) already declares a `HEALTHCHECK` using
  in-image Python against `http://127.0.0.1:8080/health`. Compose can omit a redundant
  `healthcheck:` block to avoid drift — the image self-reports health. (If a compose override
  is wanted for tuning intervals, that is acceptable; this plan omits it and notes why.)

---

## Task 1: Rewrite `docker-compose.yml`

**Files:**
- Rewrite: `docker-compose.yml`
- Modify: `.gitignore` (add `msm_password.txt`)

**What this replaces:** the legacy `plex-monitor` compose which pulled
`ghcr.io/doctorkomodo/syno_plex_change_monitor:latest`, wired Plex-specific env vars
(`PLEX_SERVER`, `PLEX_LIBRARIES`, `WAIT_SEC`, `IGNORE_DIRS`, `LOGFILE`, `PLEX_TOKEN_FILE`),
and bound Synology `/volume2/*` paths. All of that goes away.

**What is preserved in spirit:**
- The Synology Container Manager `build:` caveat (do not add a `build:` key — pull only).
- The pattern of reading a secret from a file (Docker secret block) rather than inlining it.
- The `TZ` environment variable for correct log timestamps.
- The inotify-needs-local-storage warning (adapted for the new app).

### Steps

- [ ] **Step 1: Write the complete `docker-compose.yml`**

Replace `docker-compose.yml` entirely with the following content:

```yaml
# docker-compose.yml — media-scan-monitor
#
# Replaces the legacy plex-monitor Bash stack.
#
# QUICK START
# -----------
# 1. Create the config directory and chown it to the container's non-root UID:
#      mkdir -p ./config
#      chown -R 1000:1000 ./config        # UID 1000 = container user "app"
#    The app writes app.db and secret.key here at startup; if ./config is not
#    writable by UID 1000 the container will fail with a permission error.
#
# 2. Create the password file (chmod 600; add to .gitignore):
#      echo "your-password-here" > ./msm_password.txt
#      chmod 600 ./msm_password.txt
#
# 3. Edit the media volume path(s) below — host paths MUST be local storage.
#
# 4. Start:
#      docker compose up -d
#
# NOTE ON SYNOLOGY CONTAINER MANAGER
# ------------------------------------
# Container Manager attempts to build the image when a "build:" key is present in
# this file. Keep "build:" absent (commented out or omitted) and pull the published
# image instead — do not uncomment the "build: ." line.

services:

  # ---------------------------------------------------------------------------
  # Primary service
  # ---------------------------------------------------------------------------
  media-scan-monitor:
    # Pull the prebuilt image published by GitHub Actions.
    # Tag "app-v2" is the pre-release branch tag published on app-v2 pushes.
    # Once the rewrite is cut over to main, switch to a semver tag or "latest":
    #   image: ghcr.io/doctorkomodo/media-scan-monitor:latest
    #   image: ghcr.io/doctorkomodo/media-scan-monitor:v1.0.0
    image: ghcr.io/doctorkomodo/media-scan-monitor:app-v2
    # To build locally instead, uncomment the line below and comment out "image:"
    # above, then run `docker compose build` / `docker compose up --build`.
    # Leave it commented for normal use — Synology Container Manager tries to
    # build on startup when this key is present, which fails on the NAS.
    # build: .
    container_name: media-scan-monitor
    restart: unless-stopped

    # PID-1 / signal handling: "init: true" inserts a minimal init (tini) as
    # PID 1, which reaps zombie processes and correctly forwards SIGTERM.
    # Cheap robustness — always enable this.
    init: true

    ports:
      - "8080:8080"

    environment:
      # Timezone — required for correct log timestamps.
      TZ: "Europe/Oslo"

      # Password bootstrap: the app reads MSM_PASSWORD_FILE on first run to set
      # the web UI password. After that, change the password through the UI.
      # This variable points to the Docker secret mounted at /run/secrets/.
      MSM_PASSWORD_FILE: "/run/secrets/msm_password"
      # Simpler alternative (no secrets block): set MSM_PASSWORD directly.
      # NEVER leave a real password inline here — use the secrets block above
      # or set MSM_PASSWORD as an environment variable in the shell that runs
      # docker compose, e.g.:  MSM_PASSWORD=mypassword docker compose up -d
      # MSM_PASSWORD: ""

      # Optional overrides (defaults shown; uncomment to change):
      # MSM_HOST: "0.0.0.0"
      # MSM_PORT: "8080"
      # MSM_DB_PATH: "/config/app.db"
      # MSM_SECRET_KEY_FILE: "/config/secret.key"

    volumes:
      # --- Config volume -------------------------------------------------------
      # Holds app.db (SQLite database) and secret.key (Fernet encryption key).
      #
      # IMPORTANT: The container runs as non-root user "app" (UID 1000, GID 1000).
      # The host directory ./config MUST be owned by UID 1000, or the app will
      # fail at startup with a permission error when writing app.db / secret.key.
      #
      #   mkdir -p ./config
      #   chown -R 1000:1000 ./config
      #
      # A named volume (no host path) inherits correct ownership automatically:
      #   - config:/config        # use this if you don't need host access to the DB
      #
      # PERSISTENCE: Keep ./config (or the named volume) persistent across restarts.
      # secret.key encrypts all server credentials stored in app.db; losing it
      # makes every stored token undecryptable. Back up this directory.
      - ./config:/config

      # --- Media source volumes -----------------------------------------------
      # Add one bind-mount per media directory you want monitored.
      #
      # CRITICAL — inotify requires LOCAL storage:
      #   Sources MUST be local filesystem paths (e.g. /volume2/movies on Synology).
      #   inotify does NOT work over network mounts (NFS, SMB/CIFS, FUSE, etc.).
      #   If you mount a network share here, the watcher will start but never
      #   receive events — see "No events ever seen?" in the README.
      #
      # The container path (/data/media/...) is what you configure as the watched
      # folder path in the web UI, and it must match the path your media server
      # (Plex, Emby, etc.) also uses to reference the same library.
      #
      # Example (replace /path/to/your/media with the real local path on this host):
      - /path/to/your/media:/data/media:ro
      #
      # Add more mounts as needed:
      # - /volume2/tvseries:/data/tvseries:ro
      # - /volume2/music:/data/music:ro

    secrets:
      - msm_password

    # The image already declares a HEALTHCHECK using in-image Python:
    #   GET http://127.0.0.1:8080/health → {"status":"ok"} (unauthenticated)
    # Omitting a compose healthcheck here avoids duplicating/overriding the
    # image's own tuned intervals. Docker will report the image's HEALTHCHECK
    # status in `docker ps` and `docker inspect` without any compose override.

  # ---------------------------------------------------------------------------
  # Optional init sidecar — raises fs.inotify.max_user_watches (OPT-IN ONLY)
  # ---------------------------------------------------------------------------
  #
  # This service is DISABLED by default. Enable it only with:
  #   docker compose --profile init-watches up
  #
  # ╔══════════════════════════════════════════════════════════════════════════╗
  # ║  WARNING: THIS CONTAINER IS PRIVILEGED AND WRITES A HOST-GLOBAL VALUE  ║
  # ║                                                                          ║
  # ║  sysctl fs.inotify.max_user_watches affects ALL processes on the host,  ║
  # ║  not just this container. Running this in a shared or multi-tenant       ║
  # ║  environment may have unintended side effects.                           ║
  # ║                                                                          ║
  # ║  RECOMMENDED DEFAULT: set the sysctl at the host level instead:         ║
  # ║    echo "fs.inotify.max_user_watches=131072" \                           ║
  # ║         | sudo tee /etc/sysctl.d/99-inotify.conf                        ║
  # ║    sudo sysctl --system                                                  ║
  # ║                                                                          ║
  # ║  On Synology DSM, set it in a boot task (Control Panel → Task Scheduler ║
  # ║  → Triggered Task → Boot):                                               ║
  # ║    echo 131072 > /proc/sys/fs/inotify/max_user_watches                  ║
  # ║                                                                          ║
  # ║  Use this sidecar ONLY if you cannot modify host-level sysctl            ║
  # ║  (e.g. you are running Docker Desktop on macOS/Windows, or a managed    ║
  # ║  host where you cannot write to /etc/sysctl.d).                          ║
  # ╚══════════════════════════════════════════════════════════════════════════╝
  init-watches:
    profiles: ["init-watches"]
    # busybox provides sysctl with a minimal image footprint (~4 MB).
    image: busybox:stable
    # privileged is REQUIRED to write kernel parameters from inside a container.
    privileged: true
    # One-shot: run sysctl, then exit. Never restart.
    restart: "no"
    # Raises fs.inotify.max_user_watches to MSM_INOTIFY_WATCHES (default 131072).
    # Override by setting MSM_INOTIFY_WATCHES in the environment before running:
    #   MSM_INOTIFY_WATCHES=262144 docker compose --profile init-watches up
    command: >
      sysctl -w fs.inotify.max_user_watches=${MSM_INOTIFY_WATCHES:-131072}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
secrets:
  msm_password:
    # File containing the initial web UI password.
    # Create it before first run:
    #   echo "your-password-here" > ./msm_password.txt
    #   chmod 600 ./msm_password.txt
    # Add ./msm_password.txt to .gitignore — never commit a real password.
    # After first login, change the password through the web UI; the file is
    # only read on first boot when no password has been set yet.
    file: ./msm_password.txt
```

- [ ] **Step 2: Validate with `docker compose config`**

Run both validations. If Docker is available locally:

```bash
cd /home/asg/projects/syno_plex_change_monitor

# Validate the default stack (main service only):
docker compose config
# Expected: prints the resolved/merged YAML, exit 0. The "init-watches" service
# should NOT appear (it is profile-gated and excluded from the default stack).

# Validate with the init-watches profile active:
docker compose --profile init-watches config
# Expected: prints the resolved YAML including BOTH services (media-scan-monitor
# + init-watches). Exit 0.
```

If Docker is not available in the current environment, note the validation outcome:

```
Note: docker compose config could not be run (Docker not available in this
environment). The file will be linted by CI's `docker compose config` step
in the docker-build.yml workflow (sub-plan 03). Proceed to commit.
```

CI in sub-plan 03 should add `docker compose config` as a lint step — flag this for
sub-plan 03 if it is not already there (see spec §D: "Compose validation: `docker compose
config` as a lint in CI").

- [ ] **Step 3: Gitignore the password secret file**

The compose `secrets:` block reads `./msm_password.txt` (the user creates it locally). It must
never be committed. The current `.gitignore` lists the legacy `plex_token.txt` but not this new
file — add it next to that line. Use `Edit` to insert it after the `plex_token.txt` line:

```diff
 # Secrets — never commit tokens, keys, or app config
 plex_token.txt
+msm_password.txt
 plex_monitor.conf
```

Verify it's ignored:

```bash
git check-ignore msm_password.txt
# Expected: prints "msm_password.txt" (exit 0 = ignored)
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "$(cat <<'EOF'
feat(compose): rewrite compose stack for Python media-scan-monitor app

Replace the legacy plex-monitor Bash stack with the Python app service:
ghcr image (app-v2 tag), config volume with UID-1000 ownership note,
local-storage-only media bind-mount example, TZ, MSM_PASSWORD_FILE via
compose secrets, and init: true. Add an opt-in profile-gated privileged
init-watches sidecar (busybox) that raises fs.inotify.max_user_watches.
Gitignore the new msm_password.txt secret file.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
EOF
)"
```

---

## Decisions recorded

| Decision | Choice | Rationale |
|---|---|---|
| Image owner | `doctorkomodo` (lowercase) | Matches the legacy image owner; GHCR lowercases automatically; contract §A notes the same owner. |
| Image tag | `app-v2` | Published pre-cutover branch tag (contract §A). Commented alternative shows `latest`/semver for post-cutover users. |
| Sidecar base image | `busybox:stable` | Ships `sysctl`; ~4 MB; no shell interpreter needed beyond the one-liner command; `stable` tag is the busybox convention for the current release. Alpine would also work but adds ~3 MB and a package manager that isn't used. |
| Compose `healthcheck:` block | Omitted | The image `Dockerfile` already declares a `HEALTHCHECK`; duplicating it in compose risks drift if timing is adjusted in the Dockerfile. Docker surfaces the image's own healthcheck in `docker ps`/`docker inspect` with no compose override needed. |
| Sidecar `network_mode` | Not set (default bridge) | The sidecar only runs `sysctl` — it never opens a network socket, so no special networking is required. `network_mode: host` would be unnecessary. |
| `depends_on` between sidecar and main | Not set | `sysctl` is near-instantaneous; the app's own watch-limit gate (`watcher/watch_limit.py`) retries if the limit isn't raised yet. Cross-profile `depends_on` in compose v2 has limited utility since profiles don't enforce ordered startup. Document instead. |
| Password secret | `secrets:` block → `msm_password.txt` | Mirrors the legacy `plex_token.txt` pattern; keeps the password off the process environment (rule 5); `MSM_PASSWORD` env var mentioned as comment-only fallback for simpler setups. |

## Cross-plan notes

- **Sub-plan 03 (CI):** the `docker compose config` lint (default + `init-watches` profile) is
  added to `ci.yml`'s `quality` job in 03 Task 1 — handled, not deferred.
- **Sub-plan 04 (README):** document `/config` ownership (`chown -R 1000:1000 ./config`),
  `secret.key` persistence warning, the local-storage inotify requirement, and both
  watch-limit paths (host `sysctl.d` default + `--profile init-watches` opt-in).
- **`msm_password.txt`:** gitignored by this sub-plan (Task 1, Step 3) — handled here, not deferred.
