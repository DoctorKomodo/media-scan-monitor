"""The ServerAdapter ABC and its result value objects (contract §7).

A "server" is a notification target (Plex, Emby, ...). Every backend-specific
detail lives in a concrete adapter; the watcher and pipeline only ever see this
ABC and the two result dataclasses below.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import httpx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest


@dataclass(frozen=True, slots=True)
class TriggerResult:
    """Outcome of a single trigger() call. ``ok`` is True only for a 2xx response."""

    ok: bool
    status_code: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class TestResult:
    """Outcome of a connectivity/auth probe (test())."""

    # Tell pytest this is not a test class despite the ``Test`` prefix (it is a
    # contract-mandated name). Not a dataclass field — no annotation — so it does
    # not affect ``__init__``/slots/eq; purely a collection-opt-out marker.
    __test__ = False

    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class LibraryOption:
    """One selectable backend library: an opaque id plus a human label."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class LibraryListResult:
    """Outcome of a list_libraries() probe — mirrors TestResult's ok/detail shape."""

    __test__ = False  # not a pytest class despite living beside Test* names

    ok: bool
    detail: str
    libraries: tuple[LibraryOption, ...] = ()


class ServerAdapter(ABC):
    """Base class for every notification target.

    Subclasses MUST set ``server_type`` + ``supported_scan_modes`` and implement
    ``trigger()`` + ``test()``. ``list_libraries()`` is optional — override it and
    set ``supports_library_discovery = True`` to enable the UI's library picker
    (default: unsupported).
    They receive an immutable ``ServerRuntime`` (decrypted secret in memory) and a
    shared ``httpx.AsyncClient`` owned by the engine.
    """

    server_type: ClassVar[ServerType]
    supported_scan_modes: ClassVar[frozenset[ScanMode]]
    supports_library_discovery: ClassVar[bool] = False

    def __init__(self, server: ServerRuntime, client: httpx.AsyncClient) -> None:
        self.server = server
        self.client = client

    @abstractmethod
    async def trigger(self, req: ScanRequest) -> TriggerResult:
        """Fire the backend's scan/refresh for ``req``."""

    @abstractmethod
    async def test(self) -> TestResult:
        """Probe auth + reachability only (no scan)."""

    async def list_libraries(self) -> LibraryListResult:
        """List selectable libraries (id + name). Default: the backend has no concept of one."""
        return LibraryListResult(ok=False, detail="not supported")
