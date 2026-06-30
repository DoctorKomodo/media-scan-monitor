"""media-scan-monitor: watch media folders and fan out scan/refresh events.

A UI-configured replacement for the original ``plex_monitor.sh`` Bash script.
It watches the union of all configured folders with one inotify watcher and
fans out targeted scan/refresh notifications to subscribing servers (Plex,
Emby, Jellyfin, Audiobookshelf, or generic webhooks).
"""

__all__ = ["APP_NAME", "__version__"]

__version__ = "0.1.0"

# Canonical name MSM uses to identify itself to the outside world (webhook payloads,
# e.g. the Sonarr/Radarr ``instanceName``). Distinct from a user's per-server label.
APP_NAME = "media-scan-monitor"
