"""rebuild_engine: calls engine.rebuild(), tolerant of RuntimeError (contract §F)."""

from typing import cast

from mediascanmonitor.engine import Engine
from mediascanmonitor.web.rebuild import rebuild_engine


class _OkEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def rebuild(self) -> None:
        self.calls += 1


class _RaisingEngine:
    async def rebuild(self) -> None:
        raise RuntimeError("Engine.rebuild() called before start()")


async def test_rebuild_engine_calls_rebuild() -> None:
    engine = _OkEngine()
    await rebuild_engine(cast(Engine, engine))
    assert engine.calls == 1


async def test_rebuild_engine_swallows_runtimeerror() -> None:
    # Must not raise: a write while the engine is detached/blocked never 500s.
    await rebuild_engine(cast(Engine, _RaisingEngine()))
