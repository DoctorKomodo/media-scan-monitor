def is_ignored(path: str, ignore_dirs: frozenset[str]) -> bool:
    """Return True if any *path segment* of ``path`` is in ``ignore_dirs``.

    Segment-aware (not substring): ``/a/@eaDir/b`` is ignored, but ``/a/foo@eaDir`` is not.
    """
    if not ignore_dirs:
        return False
    return any(segment in ignore_dirs for segment in path.split("/"))


def extension_matches(path: str, extensions: frozenset[str]) -> bool:
    """Return True if ``path``'s file extension is in ``extensions``.

    An empty ``extensions`` set means "match all extensions" (invariant 1). ``extensions`` are
    assumed normalized (lowercase, no leading dot); the comparison lowercases the file's
    extension so on-disk casing does not matter.
    """
    if not extensions:
        return True
    name = path.rsplit("/", 1)[-1]
    base, dot, ext = name.rpartition(".")
    if not dot or not base:
        # No dot at all, or a dotfile like ".hidden" (empty base) -> no real extension.
        return False
    return ext.lower() in extensions
