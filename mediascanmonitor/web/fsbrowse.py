"""Read-only directory browser core for the folder picker (UI helper, spec 2026-06-22).

Pure and dependency-free (no FastAPI, no DB) so it unit-tests in isolation. Lists the
immediate subdirectories of a path for the add/edit folder picker.

Paths are normalized (``..`` collapsed, made absolute) but symlinks are deliberately NOT
resolved: the watcher runs inside the container and watches the path as given, so resolving
a symlinked media dir to its real target could store a path the watcher never sees.
"""

import os

from pydantic import BaseModel

IGNORED_DIR_NAMES = frozenset({"@eaDir", "#snapshot"})
MAX_ENTRIES = 1000


class FsEntry(BaseModel):
    name: str
    path: str


class DirListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntry]
    truncated: bool = False


def list_directory(path: str) -> DirListing:
    """List the immediate subdirectories of ``path`` (files and ignore-dirs excluded)."""
    target = os.path.normpath(os.path.abspath(path or "/"))
    entries: list[FsEntry] = []
    truncated = False
    with os.scandir(target) as it:  # raises FileNotFoundError/NotADirectoryError/PermissionError
        for entry in it:
            if entry.name in IGNORED_DIR_NAMES:
                continue
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue  # a single unreadable/stale child must not blank the listing
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break
            entries.append(FsEntry(name=entry.name, path=os.path.join(target, entry.name)))
    entries.sort(key=lambda e: e.name.lower())
    parent = os.path.dirname(target)
    return DirListing(
        path=target,
        parent=None if parent == target else parent,  # dirname("/") == "/" → root has no parent
        entries=entries,
        truncated=truncated,
    )
