"""/api/events/recent — the recent-events read for the dashboard (contract §D/§G).

EventsBus.recent() is a non-blocking deque slice, so it is called directly (no to_thread). Every
record is mapped through the redacted EventRead — EventRecord itself carries no secret (rule 5).
"""

from fastapi import APIRouter, Depends, Query

from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.web.api_schemas import EventRead
from mediascanmonitor.web.deps import get_events_bus, require_api_auth

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
    dependencies=[Depends(require_api_auth)],
)


@router.get("/recent")
async def recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    events_bus: EventsBus = Depends(get_events_bus),
) -> list[EventRead]:
    return [EventRead.from_record(rec) for rec in events_bus.recent(limit)]
