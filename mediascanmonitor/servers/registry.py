"""Type → adapter-class registry (contract §7).

Adding a backend = define a ServerAdapter subclass in its own module and decorate
it with @register. Nothing else in the codebase needs to learn the new type.
"""

import httpx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ServerType
from mediascanmonitor.servers.base import ServerAdapter

_REGISTRY: dict[ServerType, type[ServerAdapter]] = {}


def register(cls: type[ServerAdapter]) -> type[ServerAdapter]:
    """Class decorator: index ``cls`` under its ``server_type``. Returns ``cls`` unchanged."""
    _REGISTRY[cls.server_type] = cls
    return cls


def get_adapter_class(server_type: ServerType) -> type[ServerAdapter]:
    """Return the adapter class for ``server_type`` or raise a clear ValueError."""
    try:
        return _REGISTRY[server_type]
    except KeyError:
        known = ", ".join(sorted(t.value for t in _REGISTRY)) or "(none registered)"
        raise ValueError(
            f"No server adapter registered for type {server_type.value!r}; known: {known}"
        ) from None


def create_adapter(server: ServerRuntime, client: httpx.AsyncClient) -> ServerAdapter:
    """Build the adapter instance for ``server`` using the shared ``client``."""
    return get_adapter_class(server.type)(server, client)
