import asyncio


class ManualClock:
    """A controllable virtual clock whose ``sleep`` only resolves when ``advance`` is called.

    Inject ``clock.sleep`` into ``Debouncer(..., sleep=clock.sleep)`` and drive timers with
    ``await clock.advance(seconds)``. No real wall-clock time elapses.
    """

    def __init__(self) -> None:
        self._now: float = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        entry: tuple[float, asyncio.Future[None]] = (self._now + delay, future)
        self._sleepers.append(entry)
        try:
            await future
        except asyncio.CancelledError:
            if entry in self._sleepers:
                self._sleepers.remove(entry)
            raise

    async def advance(self, seconds: float) -> None:
        # Let freshly-scheduled tasks reach their sleep() registration first.
        await self._settle()
        self._now += seconds
        for deadline, future in list(self._sleepers):
            if deadline <= self._now and not future.done():
                future.set_result(None)
        self._sleepers = [(d, f) for (d, f) in self._sleepers if not f.done()]
        # Let woken coroutines run to their next suspension point / completion.
        await self._settle()

    @staticmethod
    async def _settle() -> None:
        for _ in range(5):
            await asyncio.sleep(0)
