"""Pure path/extension normalization helpers (contract section 1.1).

Leaf module: imports nothing from the rest of the package, so both `db` (sub-plan 01) and
`config` (sub-plan 02) can depend down onto it without a cycle.
"""

from __future__ import annotations

import os


def normalize_extension(ext: str) -> str:
    """Strip leading dot(s), lowercase, strip surrounding whitespace. ``".MKV"`` -> ``"mkv"``."""
    return ext.strip().lstrip(".").strip().lower()


def normalize_path(path: str) -> str:
    """Pure lexical normalize: strip surrounding whitespace, collapse redundant
    separators, resolve ``.``/``..``, strip the trailing slash (except root).
    ``os.path.normpath`` semantics on the stripped string.

    Intentional non-goals (contract section 1.1): does NOT read the CWD, does NOT touch
    the filesystem, does NOT resolve symlinks, and does NOT make a relative path absolute.
    "Path must be absolute" is enforced at the schema boundary (``FolderCreate``), not here,
    so this function stays pure, total, and deterministic.
    """
    return os.path.normpath(path.strip())
