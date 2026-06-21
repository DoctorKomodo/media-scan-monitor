"""Rebuild-on-write helper (contract §F).

Every successful config mutation calls this so the running engine picks up the change with no
restart. TOLERANT: an engine that has not started (or is mid-teardown) raises RuntimeError from
rebuild(); we log and no-op so a write never 500s because the watcher is detached. After sub-plan
03's gate-recovery lands, rebuild() itself handles the blocked state internally; this guard stays
as defense-in-depth.
"""

import structlog

from mediascanmonitor.engine import Engine

log = structlog.get_logger("web")


async def rebuild_engine(engine: Engine) -> None:
    try:
        await engine.rebuild()
    except RuntimeError:
        log.info("web.rebuild_skipped")
