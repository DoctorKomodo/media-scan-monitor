"""Server adapters: the ``ServerAdapter`` ABC, registry, and per-type adapters.

Adding a new server type = one new module here implementing ``ServerAdapter``
plus a registry entry plus tests; nothing in the watcher or pipeline changes.

Populated in Phase 1 (`base`, `registry`, `http`, `plex`) and Phase 2
(`emby`, `jellyfin`, `audiobookshelf`, `webhook`).
"""

# Importing the concrete adapter modules triggers their @register decorators so
# create_adapter() can find them. Add one line here per new server type.
from mediascanmonitor.servers import (
    audiobookshelf as _audiobookshelf,  # noqa: F401  (registration side effect)
)
from mediascanmonitor.servers import emby as _emby  # noqa: F401  (registration side effect)
from mediascanmonitor.servers import jellyfin as _jellyfin  # noqa: F401  (registration side effect)
from mediascanmonitor.servers import plex as _plex  # noqa: F401  (registration side effect)
from mediascanmonitor.servers import webhook as _webhook  # noqa: F401  (registration side effect)
