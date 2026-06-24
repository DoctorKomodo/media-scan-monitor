"""Shared one-shot *probes* of a live server config (connectivity test + library listing).

Build a throwaway runtime/adapter, run one operation, always close the client. The JSON
``/api/*`` and HTML ``/ui/*`` surfaces both call these so they never drift.
"""

from collections.abc import Awaitable, Callable

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import Server
from mediascanmonitor.db.schemas import ServerCreate
from mediascanmonitor.servers.base import LibraryListResult, ServerAdapter
from mediascanmonitor.servers.http import build_client
from mediascanmonitor.servers.registry import create_adapter
from mediascanmonitor.web.api_schemas import ServerTestResponse


def runtime_from_server(server: Server, secret: str | None) -> ServerRuntime:
    """Build a runtime from a stored row + its decrypted secret (the /api + detail path)."""
    assert server.id is not None
    return ServerRuntime(
        server_id=server.id,
        name=server.name,
        type=server.type,
        base_url=server.base_url,
        verify_tls=server.verify_tls,
        timeout_seconds=server.timeout_seconds,
        secret=secret,
        scan_mode=server.scan_mode,
        debounce_mode=server.debounce_mode,
        debounce_window_seconds=server.debounce_window_seconds,
        retry_attempts=server.retry_attempts,
        webhook_method=server.webhook_method,
        webhook_headers_json=server.webhook_headers_json,
        webhook_body_template=server.webhook_body_template,
    )


def runtime_from_create(data: ServerCreate) -> ServerRuntime:
    """Build a runtime from an UNSAVED create payload (the new-server "test before save" path).

    ``server_id`` is a placeholder ``0`` — the runtime is never persisted or routed, only probed.
    The plaintext secret comes straight from the form (nothing to decrypt yet).
    """
    return ServerRuntime(
        server_id=0,
        name=data.name,
        type=data.type,
        base_url=data.base_url,
        verify_tls=data.verify_tls,
        timeout_seconds=data.timeout_seconds,
        secret=data.secret,
        scan_mode=data.scan_mode,
        debounce_mode=data.debounce_mode,
        debounce_window_seconds=data.debounce_window_seconds,
        retry_attempts=data.retry_attempts,
        webhook_method=data.webhook_method,
        webhook_headers_json=data.webhook_headers_json,
        webhook_body_template=data.webhook_body_template,
    )


async def _with_adapter[T](
    runtime: ServerRuntime, fn: Callable[[ServerAdapter], Awaitable[T]]
) -> T:
    """Build a throwaway adapter for ``runtime``, run ``fn``, and ALWAYS close the client."""
    client = build_client(verify_tls=runtime.verify_tls, timeout_seconds=runtime.timeout_seconds)
    try:
        return await fn(create_adapter(runtime, client))
    finally:
        await client.aclose()


async def run_connectivity_test(runtime: ServerRuntime) -> ServerTestResponse:
    """Probe a server via its registered adapter, always closing the client."""
    result = await _with_adapter(runtime, lambda adapter: adapter.test())
    return ServerTestResponse(ok=result.ok, detail=result.detail)


async def run_library_listing(runtime: ServerRuntime) -> LibraryListResult:
    """List a server's libraries via its adapter (twin of run_connectivity_test)."""

    async def _list(adapter: ServerAdapter) -> LibraryListResult:
        if not adapter.supports_library_discovery:
            return LibraryListResult(ok=False, detail="This server type has no libraries to list.")
        return await adapter.list_libraries()

    return await _with_adapter(runtime, _list)
