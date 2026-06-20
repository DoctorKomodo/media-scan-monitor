import structlog

from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TriggerResult

log = structlog.get_logger(__name__)


class Dispatcher:
    """Fan a single ``ScanRequest`` to its server's adapter, isolating all failures.

    Invariant 6: one bad server never raises out of ``dispatch`` or aborts the event loop. A
    missing adapter or an adapter exception becomes ``TriggerResult(ok=False, ...)``. Only
    ``Exception`` is caught so ``asyncio.CancelledError`` still propagates for clean shutdown.
    """

    def __init__(self, adapters: dict[int, ServerAdapter]) -> None:
        self._adapters = adapters

    async def dispatch(self, req: ScanRequest) -> TriggerResult:
        adapter = self._adapters.get(req.server_id)
        if adapter is None:
            log.warning(
                "dispatch.no_adapter",
                server_id=req.server_id,
                server_name=req.server_name,
                scan_key=req.scan_key,
            )
            return TriggerResult(
                ok=False,
                status_code=None,
                detail=f"no adapter for server_id={req.server_id}",
            )
        try:
            return await adapter.trigger(req)
        except Exception as exc:  # isolate: never propagate a per-server failure
            log.warning(
                "dispatch.adapter_error",
                server_id=req.server_id,
                server_name=req.server_name,
                scan_key=req.scan_key,
                error=repr(exc),
            )
            return TriggerResult(ok=False, status_code=None, detail=f"adapter raised: {exc!r}")

    def set_adapters(self, adapters: dict[int, ServerAdapter]) -> None:
        """Swap the adapter map atomically (used by ``Engine.rebuild`` on config change)."""
        self._adapters = adapters
