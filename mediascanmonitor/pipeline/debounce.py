import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import DebounceMode
from mediascanmonitor.pipeline.events import ScanRequest


class Debouncer:
    """Per-server debounce applied after routing.

    ``off``      -> await ``dispatch(req)`` immediately (no coalescing).
    ``trailing`` -> coalesce per ``(server_id, scan_key)`` with classic trailing-edge semantics:
                    each ``submit`` cancels any pending timer for the key and arms a fresh
                    ``window``-second timer; the dispatch fires once, ``window`` seconds after
                    the most recent event, carrying the most recent request.

    A server id with no registered ``ServerRuntime`` fails open (immediate dispatch) rather than
    silently dropping the event. ``sleep`` is injectable so tests drive a fake clock.
    """

    def __init__(
        self,
        dispatch: Callable[[ScanRequest], Awaitable[None]],
        servers: dict[int, ServerRuntime],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._dispatch = dispatch
        self._servers = servers
        self._sleep = sleep
        self._timers: dict[tuple[int, str], asyncio.Task[None]] = {}

    async def submit(self, req: ScanRequest) -> None:
        server = self._servers.get(req.server_id)
        if server is None or server.debounce_mode is DebounceMode.off:
            await self._dispatch(req)
            return

        window = float(server.debounce_window_seconds)
        key = (req.server_id, req.scan_key)
        pending = self._timers.get(key)
        if pending is not None:
            pending.cancel()
        self._timers[key] = asyncio.create_task(self._fire_after(key, req, window))

    def update_servers(self, servers: dict[int, ServerRuntime]) -> None:
        """Swap the per-server policy map in place on ``Engine.rebuild`` (contract §9/§10),
        keeping this Debouncer instance and its pending timers. A pending
        ``(server_id, scan_key)`` whose server is **gone** from ``servers`` is cancelled (the
        server was disabled/deleted — do not dispatch). Survivors keep their armed timer; the
        new window length is read only when a key next (re)arms, not retroactively.
        """
        self._servers = servers
        for key in list(self._timers):
            if key[0] not in servers:
                task = self._timers.pop(key, None)
                if task is not None:
                    task.cancel()

    async def _fire_after(self, key: tuple[int, str], req: ScanRequest, window: float) -> None:
        try:
            await self._sleep(window)
        except asyncio.CancelledError:
            return
        # Only fire if we are still the active timer for this key (guards a re-arm that landed
        # in the same loop turn the window elapsed -> never double-dispatch).
        if self._timers.get(key) is not asyncio.current_task():
            return
        del self._timers[key]
        await self._dispatch(req)

    async def aclose(self) -> None:
        timers = list(self._timers.values())
        self._timers.clear()
        for task in timers:
            task.cancel()
        for task in timers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
