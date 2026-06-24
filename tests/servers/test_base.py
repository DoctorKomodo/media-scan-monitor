"""Shape tests for the ServerAdapter ABC and result dataclasses."""

import dataclasses

import httpx
import pytest

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult

from .conftest import make_plex_runtime


def test_trigger_result_is_frozen_and_slotted() -> None:
    r = TriggerResult(ok=True, status_code=200, detail="ok")
    assert (r.ok, r.status_code, r.detail) == (True, 200, "ok")
    assert not hasattr(r, "__dict__")  # slots=True
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = False  # type: ignore[misc]


def test_test_result_is_frozen_and_slotted() -> None:
    r = TestResult(ok=False, detail="nope")
    assert (r.ok, r.detail) == (False, "nope")
    assert not hasattr(r, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.detail = "changed"  # type: ignore[misc]


def test_serveradapter_declares_the_two_abstract_methods() -> None:
    assert ServerAdapter.__abstractmethods__ == frozenset({"trigger", "test"})
    assert getattr(ServerAdapter.trigger, "__isabstractmethod__", False)
    assert getattr(ServerAdapter.test, "__isabstractmethod__", False)


async def test_concrete_subclass_stores_server_and_client(
    client: httpx.AsyncClient,
) -> None:
    class _Dummy(ServerAdapter):
        server_type = ServerType.plex
        supported_scan_modes = frozenset({ScanMode.targeted})

        async def trigger(self, req: ScanRequest) -> TriggerResult:
            return TriggerResult(ok=True, status_code=200, detail="ok")

        async def test(self) -> TestResult:
            return TestResult(ok=True, detail="ok")

    runtime = make_plex_runtime()
    adapter = _Dummy(runtime, client)
    assert adapter.server is runtime
    assert adapter.client is client
    assert _Dummy.supported_scan_modes == frozenset({ScanMode.targeted})


async def test_default_list_libraries_is_unsupported(client: httpx.AsyncClient) -> None:
    # The webhook adapter does not override list_libraries(), so it inherits the ABC default.
    runtime = make_plex_runtime(
        type=ServerType.webhook, base_url="", scan_mode=ScanMode.library, secret=None
    )
    adapter = registry.create_adapter(runtime, client)
    assert adapter.supports_library_discovery is False
    result = await adapter.list_libraries()
    assert result.ok is False
    assert result.detail == "not supported"
    assert result.libraries == ()
