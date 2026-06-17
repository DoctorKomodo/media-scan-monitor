"""Server adapters: the ``ServerAdapter`` ABC, registry, and per-type adapters.

Adding a new server type = one new module here implementing ``ServerAdapter``
plus a registry entry plus tests; nothing in the watcher or pipeline changes.

Populated in Phase 1 (`base`, `registry`, `http`, `plex`) and Phase 2
(`emby`, `jellyfin`, `audiobookshelf`, `webhook`).
"""
