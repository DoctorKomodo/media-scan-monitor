# Synology Plex Change Monitor

A lightweight bash script that monitors media directories on a Synology NAS and triggers **targeted partial scans** in Plex when files are added, moved, or deleted. It replaces Plex's built-in filesystem monitoring, which is often unreliable over networks.

## Why?

Plex's built-in "Update my library automatically" option relies on filesystem notifications (inotify) to detect changes. These notifications do not propagate over network filesystems like NFS or SMB — so when media is added to your Synology NAS from another machine over the network, Plex never sees the change. New media won't appear until the next scheduled full library scan.

This script solves that by running `inotifywait` directly on the NAS itself, where local filesystem events are always available, and calling Plex's partial scan API to refresh only the specific show or movie folder that changed — not the entire library.

## How It Works

1. `inotifywait` recursively watches your media directories for file create, move, and delete events.
2. Events are filtered to media file extensions only (video and subtitle formats).
3. Synology system directories (`@eaDir`, `#snapshot`) are ignored.
4. The script determines which Plex library and top-level folder (e.g., the show or movie folder) the change belongs to.
5. A **debounce** mechanism ensures that rapid changes (like copying an entire season) trigger only a single scan after a configurable wait period (default: 30 seconds).
6. A targeted Plex API call refreshes just that folder.

### A note on paths

The partial-scan API call sends Plex a path, and **that path must match how Plex itself sees the media**, not how the NAS sees it. If Plex runs in its own container with the library mounted at `/data/media/tvseries`, then the monitor must also refer to the media as `/data/media/tvseries` — otherwise Plex can't match the path to a library and silently falls back to a full scan (or does nothing).

The script only ever works with these *Plex-facing* paths (configured via `PLEX_LIBRARIES`). When running in Docker you simply bind-mount the NAS media to those same paths inside the monitor container, so everything lines up. The host-side source of each mount is independent of the script.

## Configuration

All settings are read from environment variables. When running bare-metal, these can be placed in a file that the script sources via `CONFIG_FILE` (see below).

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_SERVER` | — (required) | Full Plex server URL including port, e.g. `https://host:32400` |
| `PLEX_LIBRARIES` | — (required) | Comma-separated `path:section_id` pairs, e.g. `/data/media/movies:1,/data/media/tvseries:2`. Defines both what to watch and how to map each path to a Plex library. |
| `PLEX_TOKEN` | — | Plex auth token. Provide this **or** `PLEX_TOKEN_FILE`. |
| `PLEX_TOKEN_FILE` | — | Path to a file containing the token (used for Docker secrets, e.g. `/run/secrets/plex_token`). Takes precedence over `PLEX_TOKEN`. |
| `MEDIA_EXTENSIONS` | `mkv\|mp4\|avi\|...` | Pipe-separated list of file extensions to watch |
| `IGNORE_DIRS` | `@eaDir,#snapshot` | Comma-separated directory names to skip |
| `WAIT_SEC` | `30` | Debounce interval in seconds |
| `LOGFILE` | `/dev/stdout` | Log destination. Default streams to stdout for `docker logs`; set a path for bare-metal. |
| `REQUIRED_INOTIFY_WATCHES` | `131072` | Watch limit the script waits for before starting. Set `0` to skip the check. |
| `INOTIFY_WAIT_MAX` | `180` | How long (seconds) to wait for the watch limit before exiting. |
| `CONFIG_FILE` | — | Optional path to a shell file that is `source`d before reading the variables above (bare-metal convenience / backward compatibility). |

To find your library section IDs, visit:
```
https://your-plex-server:32400/library/sections?X-Plex-Token=YOUR_TOKEN
```

---

## Option A: Docker (recommended)

Runs the monitor as a container on the NAS. Configuration lives in `docker-compose.yml`; the token lives in a Docker secret. Changing libraries or mount paths is a compose edit — the script never changes.

### 1. Provide the token

```bash
cp plex_token.txt.example plex_token.txt
# paste your real Plex token on a single line, then:
chmod 600 plex_token.txt
```

`plex_token.txt` is gitignored so it never gets committed.

### 2. Edit `docker-compose.yml`

Set `PLEX_SERVER`, `PLEX_LIBRARIES`, and the volume mounts. The key rule for the volumes:

```yaml
volumes:
  # host (NAS) source : container target
  - /volume2/movies:/data/media/movies
  - /volume2/tvseries:/data/media/tvseries
```

- The **target** (right side) must match the path Plex uses and the paths in `PLEX_LIBRARIES`. This is the stable contract — don't change it unless Plex's own mount changes.
- The **source** (left side) is wherever the media lives on the NAS. Sources must be **local** `/volume2` paths so inotify events fire (a network-mounted source won't work).

### 3. Start it

By default the compose file pulls the prebuilt image published by GitHub Actions:

```bash
docker compose pull
docker compose up -d
docker compose logs -f
```

To build locally instead (e.g. to test changes), uncomment the `build: .` line in `docker-compose.yml`, then:

```bash
docker compose up -d --build
```

> Keep `build:` commented for normal use. Synology Container Manager attempts to build on startup whenever it's present, which fails on the NAS — so the default is to pull the prebuilt image only.

> If the GHCR package is private, run `docker login ghcr.io` on the NAS (with a PAT that has `read:packages`) before pulling, or set the package visibility to public on GitHub.

### 4. Set the inotify watch limit on the host

This is a host kernel setting and **cannot** be set from inside the container or via compose. See [Increase the inotify watch limit](#increase-the-inotify-watch-limit) below — it applies to both deployment options.

### Building / publishing the image

A GitHub Actions workflow (`.github/workflows/docker-build.yml`) builds a multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it to GitHub Container Registry on every push to `main` and on version tags (`vX.Y.Z`). It uses the built-in `GITHUB_TOKEN`, so no extra secrets are required.

---

## Option B: Bare-metal on DSM

Runs the script directly on the NAS via Task Scheduler.

### 1. Place the files

```
/volume2/scripts/
├── plex_monitor.sh        # Main script
├── plex_monitor.conf      # Configuration + token (keep secret)
└── logs/                  # Create this directory
    └── plex_notify.log
```

### 2. Create the config file

Because the script sources `CONFIG_FILE`, you can keep **all** settings in `plex_monitor.conf`:

```bash
PLEX_SERVER="https://your-server.plex.direct:32400"
PLEX_LIBRARIES="/volume2/movies:1,/volume2/tvseries:2"
PLEX_TOKEN="your_token_here"
LOGFILE="/volume2/scripts/logs/plex_notify.log"
```

Then restrict permissions:

```bash
chmod 600 /volume2/scripts/plex_monitor.conf
```

> Bare-metal note: when Plex runs on the same NAS and sees the media at its real `/volume2/...` paths, use those paths in `PLEX_LIBRARIES`. If Plex runs elsewhere with different mount paths, use the paths *Plex* sees (see [A note on paths](#a-note-on-paths)).

### 3. Increase the inotify watch limit

See [below](#increase-the-inotify-watch-limit).

### 4. Start the monitor on boot

Create a **Triggered Task** in DSM Task Scheduler:

| Setting | Value |
|---------|-------|
| Event | Boot-up |
| User | *your regular user* (not root) |
| Command | `CONFIG_FILE=/volume2/scripts/plex_monitor.conf bash /volume2/scripts/plex_monitor.sh` |

The script waits for the inotify limit to be set before it begins monitoring.

### 5. Set up log rotation (optional)

When logging to a file (not stdout), create `/etc/logrotate.d/plex_monitor`:

```
/volume2/scripts/logs/plex_notify.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    size 10M
}
```

(Under Docker this is unnecessary — Docker's logging driver handles rotation.)

---

## Increase the inotify watch limit

Large media libraries require a higher inotify watch limit than the default. This is a **host kernel setting** shared by the host and all containers; it cannot be set from inside an unprivileged container. Create a **Triggered Task** in DSM Task Scheduler:

| Setting | Value |
|---------|-------|
| Event | Boot-up |
| User | root |
| Command | `sh -c '(sleep 90 && echo 131072 > /proc/sys/fs/inotify/max_user_watches)&'` |

The 90-second delay ensures the system is fully booted before writing to `/proc`. The monitor (in either deployment) waits for this value before it starts watching.

## Requirements

- Synology NAS running DSM
- **Docker** (for Option A) — available as Container Manager in DSM
- For bare-metal (Option B):
  - `bash` 4+ (for associative arrays)
  - `inotifywait` — from the SynoCommunity `inotify-tools` package
  - `curl`
  - `python3` (used for URL encoding)
- A Plex Media Server accessible on the network

## Monitored Events

| Event | Trigger |
|-------|---------|
| `CREATE` | New file appears in a watched directory |
| `MOVED_TO` | File moved into a watched directory |
| `DELETE` | File removed from a watched directory |
| `MOVE` | File moved out of a watched directory |

## Supported File Types

Video: `mkv`, `mp4`, `avi`, `ts`, `m4v`, `mov`, `wmv`, `flv`, `webm`

Subtitles: `srt`, `smi`, `ssa`, `ass`, `sub`, `idx`, `sup`, `vtt`

## Troubleshooting

**Script exits immediately with inotify limit error**
The boot task that sets the inotify limit may not have run yet. The script waits up to `INOTIFY_WAIT_MAX` seconds (default 180). Check that the root boot task is enabled. To bypass the check, set `REQUIRED_INOTIFY_WATCHES=0`.

**Changes not detected**
Verify `inotifywait` sees the files. In Docker, confirm the media is bind-mounted from a **local** `/volume2` source (network-mounted sources don't emit inotify events). Test manually:
```bash
inotifywait -m -r /volume2/movies/
```

**Plex refresh returns non-200, or new media appears only after a full scan**
The path the script sends must match how Plex sees the media. If they don't match, Plex can't target the folder. Confirm `PLEX_LIBRARIES` paths equal the library folders configured in Plex, and verify the token:
```bash
curl -s "https://your-server:32400/library/sections?X-Plex-Token=YOUR_TOKEN"
```

**Container can't pull the image**
If the GHCR package is private, `docker login ghcr.io` on the NAS or make the package public.

## License

MIT
