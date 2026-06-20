"""Pure config defaults: ignore-dirs, extension presets, per-type debounce policy.

Constants only — the path/extension normalizers live in the leaf module
`mediascanmonitor.normalize` (sub-plan 01). This module imports the enums from
`db.models` and nothing else from the package; keep it import-light and pure.
"""

from mediascanmonitor.db.models import DebounceMode, ServerType

# Synology (and similar NAS) system directories that must never trigger a scan.
IGNORE_DIRS: frozenset[str] = frozenset({"@eaDir", "#snapshot", "#recycle", "@tmp"})

# Suggested, already-normalized extension sets offered as UI presets (Phase 3).
EXTENSION_PRESETS: dict[str, tuple[str, ...]] = {
    "video": ("mkv", "mp4", "avi", "ts", "m4v", "mov", "wmv", "flv", "webm"),
    "subtitles": ("srt", "smi", "ssa", "ass", "sub", "idx", "sup", "vtt"),
    "audio": ("mp3", "flac", "m4b", "m4a", "aac", "ogg", "opus", "wav"),
}

# Default trailing-debounce window (seconds) when a server uses trailing mode.
DEFAULT_DEBOUNCE_WINDOW_SECONDS: int = 30

# Per-server-type default debounce policy. Media servers collapse bursts (trailing);
# generic webhooks want every event (off). Overridable per server in the UI (Phase 3).
DEFAULT_DEBOUNCE_BY_TYPE: dict[ServerType, DebounceMode] = {
    ServerType.webhook: DebounceMode.off,
    ServerType.plex: DebounceMode.trailing,
    ServerType.emby: DebounceMode.trailing,
    ServerType.jellyfin: DebounceMode.trailing,
    ServerType.audiobookshelf: DebounceMode.trailing,
}
