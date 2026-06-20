"""inotify `max_user_watches` gate.

Per-directory watches consume the kernel `fs.inotify.max_user_watches` budget.
These helpers count the directories a config will watch and compare against the
current limit (with headroom) so the engine/dashboard can surface a clear
"raise your watch limit" signal — re-implementing the legacy script's gate.
"""

import os
from collections.abc import Iterable
from pathlib import Path


def read_max_user_watches(proc_path: str = "/proc/sys/fs/inotify/max_user_watches") -> int:
    """Return the current `max_user_watches` kernel limit."""
    return int(Path(proc_path).read_text().strip())


def count_dirs(roots: Iterable[str], ignore_dirs: frozenset[str]) -> int:
    """Count directories that will be watched: each root plus every descendant
    directory, skipping any directory named in `ignore_dirs` (and its subtree).
    Missing roots contribute zero.
    """
    total = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for _dirpath, dirnames, _filenames in os.walk(root):
            # Prune ignored directories in place so os.walk never descends them.
            dirnames[:] = [name for name in dirnames if name not in ignore_dirs]
            total += 1  # count the current directory (root counted once at the top)
    return total
