from mediascanmonitor.db.models import DebounceMode, ScanMode
from mediascanmonitor.pipeline.debounce import Debouncer
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from tests.pipeline.clock import ManualClock
from tests.pipeline.factories import make_server_runtime


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ScanRequest] = []

    async def __call__(self, req: ScanRequest) -> None:
        self.calls.append(req)


def _req(server_id: int, scan_key: str, *, file_path: str = "/data/tv/Show/ep.mkv") -> ScanRequest:
    return ScanRequest(
        server_id=server_id,
        server_name="plex-1",
        scan_mode=ScanMode.targeted,
        scan_path=scan_key,
        library_id="2",
        scan_key=scan_key,
        event_type=FsEventType.created,
        file_path=file_path,
        top_folder="Show",
    )


async def test_off_mode_dispatches_every_event_immediately() -> None:
    servers = {1: make_server_runtime(server_id=1, debounce_mode=DebounceMode.off)}
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    for _ in range(3):
        await debouncer.submit(_req(1, "/data/tv/Show"))

    # off mode bypasses the timer: all three dispatched without advancing the clock.
    assert len(recorder.calls) == 3
    await debouncer.aclose()


async def test_trailing_collapses_a_burst_into_one_dispatch() -> None:
    servers = {
        1: make_server_runtime(
            server_id=1, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        )
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    for i in range(5):
        await debouncer.submit(_req(1, "/data/tv/Show", file_path=f"/data/tv/Show/ep{i}.mkv"))

    assert recorder.calls == []  # nothing fires before the window elapses
    await clock.advance(30)
    assert len(recorder.calls) == 1  # one dispatch for the whole burst
    assert recorder.calls[0].file_path == "/data/tv/Show/ep4.mkv"  # the most-recent request
    await debouncer.aclose()


async def test_trailing_distinct_scan_keys_debounce_independently() -> None:
    servers = {
        1: make_server_runtime(
            server_id=1, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        )
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/ShowA"))
    await debouncer.submit(_req(1, "/data/tv/ShowB"))

    await clock.advance(30)
    assert {r.scan_key for r in recorder.calls} == {"/data/tv/ShowA", "/data/tv/ShowB"}
    assert len(recorder.calls) == 2
    await debouncer.aclose()


async def test_trailing_resets_the_window_on_each_event() -> None:
    # Proves reset-on-each-event semantics (not a fixed window from the first event).
    servers = {
        1: make_server_runtime(
            server_id=1, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        )
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/Show", file_path="/data/tv/Show/first.mkv"))
    await clock.advance(10)  # 10s in, first timer would fire at 30
    assert recorder.calls == []

    await debouncer.submit(_req(1, "/data/tv/Show", file_path="/data/tv/Show/second.mkv"))
    await clock.advance(20)  # now 30s from first event, 20s from second
    assert recorder.calls == []  # NOT fired -> the window was reset by event 2

    await clock.advance(10)  # now 30s from the second event
    assert len(recorder.calls) == 1
    assert recorder.calls[0].file_path == "/data/tv/Show/second.mkv"
    await debouncer.aclose()


async def test_aclose_cancels_pending_timers_without_dispatching() -> None:
    servers = {
        1: make_server_runtime(
            server_id=1, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        )
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/Show"))
    await debouncer.aclose()
    await clock.advance(30)
    assert recorder.calls == []  # pending timer was cancelled and dropped


async def test_unknown_server_falls_back_to_immediate_dispatch() -> None:
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, {}, sleep=clock.sleep)  # no servers registered

    await debouncer.submit(_req(99, "/data/tv/Show"))
    assert len(recorder.calls) == 1  # fail-open: deliver rather than silently drop
    await debouncer.aclose()


async def test_update_servers_drops_timers_for_removed_servers() -> None:
    # Engine.rebuild swaps the server map in place; a removed server's pending timer is dropped.
    servers = {
        1: make_server_runtime(
            server_id=1, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        ),
        2: make_server_runtime(
            server_id=2, debounce_mode=DebounceMode.trailing, debounce_window_seconds=30
        ),
    }
    recorder = Recorder()
    clock = ManualClock()
    debouncer = Debouncer(recorder, servers, sleep=clock.sleep)

    await debouncer.submit(_req(1, "/data/tv/ShowA"))
    await debouncer.submit(_req(2, "/data/tv/ShowB"))

    debouncer.update_servers({1: servers[1]})  # server 2 removed on rebuild
    await clock.advance(30)

    assert {r.server_id for r in recorder.calls} == {1}  # server 2's timer cancelled, not fired
    await debouncer.aclose()
