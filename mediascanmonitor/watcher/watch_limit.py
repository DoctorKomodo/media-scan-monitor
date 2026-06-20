"""inotify `max_user_watches` gate.

Per-directory watches consume the kernel `fs.inotify.max_user_watches` budget.
These helpers count the directories a config will watch and compare against the
current limit (with headroom) so the engine/dashboard can surface a clear
"raise your watch limit" signal — re-implementing the legacy script's gate.
"""

from pathlib import Path


def read_max_user_watches(proc_path: str = "/proc/sys/fs/inotify/max_user_watches") -> int:
    """Return the current `max_user_watches` kernel limit."""
    return int(Path(proc_path).read_text().strip())
