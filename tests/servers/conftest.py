"""Shared builders and fixtures for the server-adapter tests.

The builders construct the FROZEN contract types (ServerRuntime / ScanRequest)
with full keyword signatures so mypy --strict stays happy (no **dict splatting).
"""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest


def make_plex_runtime(
    *,
    server_id: int = 1,
    name: str = "My Plex",
    type: ServerType = ServerType.plex,
    base_url: str = "https://plex.example:32400",
    verify_tls: bool = True,
    timeout_seconds: float = 10.0,
    secret: str | None = "tok-secret",
    scan_mode: ScanMode = ScanMode.targeted,
    debounce_mode: DebounceMode = DebounceMode.trailing,
    debounce_window_seconds: int = 30,
    retry_attempts: int = 1,
    webhook_method: str | None = None,
    webhook_headers_json: str | None = None,
    webhook_body_template: str | None = None,
) -> ServerRuntime:
    """Build a ServerRuntime; defaults to an enabled Plex server with no retries."""
    return ServerRuntime(
        server_id=server_id,
        name=name,
        type=type,
        base_url=base_url,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
        secret=secret,
        scan_mode=scan_mode,
        debounce_mode=debounce_mode,
        debounce_window_seconds=debounce_window_seconds,
        retry_attempts=retry_attempts,
        webhook_method=webhook_method,
        webhook_headers_json=webhook_headers_json,
        webhook_body_template=webhook_body_template,
    )


def make_scan_request(
    *,
    server_id: int = 1,
    server_name: str = "My Plex",
    scan_mode: ScanMode = ScanMode.targeted,
    scan_path: str | None = "/data/media/tvseries/Tom & Jerry",
    library_id: str | None = "2",
    scan_key: str = "/data/media/tvseries/Tom & Jerry",
    event_type: FsEventType = FsEventType.created,
    file_path: str = "/data/media/tvseries/Tom & Jerry/ep01.mkv",
    top_folder: str | None = "Tom & Jerry",
) -> ScanRequest:
    """Build a ScanRequest; defaults to a targeted Plex scan with a space + '&' in the path."""
    return ScanRequest(
        server_id=server_id,
        server_name=server_name,
        scan_mode=scan_mode,
        scan_path=scan_path,
        library_id=library_id,
        scan_key=scan_key,
        event_type=event_type,
        file_path=file_path,
        top_folder=top_folder,
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """A real httpx.AsyncClient; respx patches its transport when @respx.mock is active."""
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def clean_registry() -> AsyncIterator[None]:
    """Snapshot and restore the adapter registry so tests that register dummies don't leak."""
    from mediascanmonitor.servers import registry

    saved = dict(registry._REGISTRY)
    try:
        yield None
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)
