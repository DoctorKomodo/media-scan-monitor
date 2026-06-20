from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.dispatcher import Dispatcher
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult


def _req(server_id: int) -> ScanRequest:
    return ScanRequest(
        server_id=server_id,
        server_name="plex-1",
        scan_mode=ScanMode.targeted,
        scan_path="/data/tv/Show",
        library_id="2",
        scan_key="/data/tv/Show",
        event_type=FsEventType.created,
        file_path="/data/tv/Show/ep.mkv",
        top_folder="Show",
    )


class OkAdapter(ServerAdapter):
    server_type = ServerType.plex
    supported_scan_modes = frozenset({ScanMode.targeted, ScanMode.library})

    def __init__(self) -> None:  # no httpx client needed for this fake
        self.calls: list[ScanRequest] = []

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        self.calls.append(req)
        return TriggerResult(ok=True, status_code=200, detail="ok")

    async def test(self) -> TestResult:
        return TestResult(ok=True, detail="ok")


class FaultyAdapter(ServerAdapter):
    server_type = ServerType.plex
    supported_scan_modes = frozenset({ScanMode.targeted})

    def __init__(self) -> None:
        pass

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        raise RuntimeError("boom")

    async def test(self) -> TestResult:
        return TestResult(ok=False, detail="boom")


async def test_dispatch_calls_matching_adapter_and_returns_its_result() -> None:
    adapter = OkAdapter()
    dispatcher = Dispatcher({1: adapter})

    result = await dispatcher.dispatch(_req(1))

    assert result.ok is True
    assert result.status_code == 200
    assert len(adapter.calls) == 1


async def test_dispatch_isolates_adapter_exceptions() -> None:
    dispatcher = Dispatcher({1: FaultyAdapter()})

    result = await dispatcher.dispatch(_req(1))  # must NOT raise

    assert result.ok is False
    assert result.status_code is None
    assert "boom" in result.detail


async def test_dispatch_unknown_server_id_returns_failure_not_raise() -> None:
    dispatcher = Dispatcher({1: OkAdapter()})

    result = await dispatcher.dispatch(_req(999))

    assert result.ok is False
    assert result.status_code is None
    assert "999" in result.detail


async def test_set_adapters_swaps_the_adapter_map() -> None:
    old = OkAdapter()
    dispatcher = Dispatcher({1: old})

    new = OkAdapter()
    dispatcher.set_adapters({2: new})

    # Old id no longer routes; new id does.
    miss = await dispatcher.dispatch(_req(1))
    assert miss.ok is False
    hit = await dispatcher.dispatch(_req(2))
    assert hit.ok is True
    assert len(old.calls) == 0
    assert len(new.calls) == 1
