"""Asyncinotify-backed recursive watcher.

Raw inotify is not recursive, so this backend adds one watch per directory under
each configured root and dynamically adds/removes watches as subdirectories are
created, moved, or deleted. When a new subdirectory appears, its existing
contents are rescanned and emitted as synthetic `created` events to close the
window between `mkdir` and `add_watch` (the "attach race").

This module performs NO extension filtering — that is the pipeline's job. It only
skips `ignore_dirs` path segments (e.g. Synology `@eaDir`/`#snapshot`) and
normalizes paths via `normalize_path`.

asyncinotify ships Linux-only C bindings, so it is imported lazily inside
`InotifyBackend.__init__`. The module top (and the pure mask helpers below)
import on any platform, which keeps the mask-mapping unit tests portable. We do
NOT use asyncinotify's `add_watch(recursive=True)`: per-directory control is
required for the watch-limit gate and for the attach-race rescan.
"""

from mediascanmonitor.pipeline.events import FsEventType

# inotify event bit constants (stable Linux kernel ABI). Defined locally so the
# pure mapping helpers need no asyncinotify import and run on any platform.
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_Q_OVERFLOW = 0x00004000   # kernel queue overflow — events were dropped; triggers a resync
IN_ISDIR = 0x40000000


def mask_to_event_type(mask: int) -> FsEventType | None:
    """Map a raw inotify event mask to an `FsEventType`, or `None` if the mask
    carries no create/move/delete signal we care about (e.g. `IN_IGNORED`).
    """
    if mask & IN_CREATE:
        return FsEventType.created
    if mask & IN_MOVED_TO:
        return FsEventType.moved_to
    if mask & IN_DELETE:
        return FsEventType.deleted
    if mask & IN_MOVED_FROM:
        return FsEventType.moved_from
    return None


def mask_is_dir(mask: int) -> bool:
    """True if the event concerns a directory (the `IN_ISDIR` bit is set)."""
    return bool(mask & IN_ISDIR)
