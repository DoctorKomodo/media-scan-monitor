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

import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mediascanmonitor.normalize import normalize_path
from mediascanmonitor.pipeline.events import FsEvent, FsEventType

if TYPE_CHECKING:
    from asyncinotify import Inotify, Watch

logger = logging.getLogger(__name__)

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


class InotifyBackend:
    """Recursive `WatcherBackend` built on asyncinotify (one watch per directory)."""

    def __init__(self, ignore_dirs: frozenset[str]) -> None:
        from asyncinotify import Inotify, Mask

        self._ignore_dirs = ignore_dirs
        self._inotify: Inotify = Inotify()
        self._add_mask = Mask.CREATE | Mask.MOVED_TO | Mask.DELETE | Mask.MOVED_FROM
        self._watches: dict[str, Watch] = {}
        self._roots: set[str] = set()

    # -- internal watch bookkeeping -----------------------------------------
    def _is_ignored(self, path: str) -> bool:
        return any(segment in self._ignore_dirs for segment in path.split(os.sep))

    def _add_watch(self, path: str) -> None:
        if path in self._watches:
            return
        try:
            self._watches[path] = self._inotify.add_watch(path, self._add_mask)
        except OSError as exc:
            # Adding a watch can fail at runtime (kernel limit / ENOSPC) even though the
            # startup gate passed, because watches grow as directories appear. Degrade:
            # log and skip — this directory is unwatched rather than crashing the watcher.
            # The dashboard's check_watch_limit surfaces the shortfall.
            logger.warning("inotify add_watch failed for %s: %s", path, exc)

    def _remove_watch_tree(self, root: str) -> None:
        prefix = root + os.sep
        doomed = [p for p in self._watches if p == root or p.startswith(prefix)]
        for path in doomed:
            watch = self._watches.pop(path)
            try:  # noqa: SIM105 - keep the explanatory comment on the swallowed error
                self._inotify.rm_watch(watch)
            except OSError:
                # When a watched dir is deleted the kernel auto-removes its watch
                # and emits IN_IGNORED; an explicit rm_watch then fails with
                # EINVAL. The watch is already gone, so this is safe to ignore.
                pass

    def _walk_add_watches(self, root: str) -> list[FsEvent]:
        """Add a watch for every non-ignored directory at/under `root`. Return
        synthetic `created` `FsEvent`s for every entry *below* `root` (the root
        itself is excluded — its own event, if any, is emitted by the caller).
        Used with an empty/discarded result at startup, and with its events
        yielded when a new subdirectory appears at runtime (attach-race close).
        """
        synthetic: list[FsEvent] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored dirs in place so os.walk never descends into them.
            dirnames[:] = [name for name in dirnames if name not in self._ignore_dirs]
            norm_dir = normalize_path(dirpath)
            if self._is_ignored(norm_dir):
                continue
            self._add_watch(norm_dir)
            if norm_dir != root:
                synthetic.append(FsEvent(norm_dir, FsEventType.created, is_dir=True))
            for name in filenames:
                fpath = normalize_path(os.path.join(dirpath, name))
                synthetic.append(FsEvent(fpath, FsEventType.created, is_dir=False))
        return synthetic

    # -- WatcherBackend protocol --------------------------------------------
    def set_roots(self, roots: set[str]) -> None:
        new_roots = {normalize_path(root) for root in roots}
        for gone in self._roots - new_roots:
            self._remove_watch_tree(gone)
        for added in new_roots - self._roots:
            if os.path.isdir(added) and not self._is_ignored(added):
                # Watch the existing tree but do NOT emit synthetic events for
                # pre-existing library content at startup.
                self._walk_add_watches(added)
        self._roots = new_roots

    async def events(self) -> AsyncIterator[FsEvent]:
        async for event in self._inotify:
            path_obj = event.path
            if path_obj is None:
                continue
            path = normalize_path(str(path_obj))
            if self._is_ignored(path):
                continue
            mask = int(event.mask)
            event_type = mask_to_event_type(mask)
            if event_type is None:
                continue
            yield FsEvent(path, event_type, is_dir=mask_is_dir(mask))

    async def aclose(self) -> None:
        self._inotify.close()
        self._watches.clear()
