"""Registry mechanics: register / get_adapter_class / create_adapter / unknown error."""

import httpx
import pytest

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult

from .conftest import make_plex_runtime


def _make_dummy_cls() -> type[ServerAdapter]:
    class _WebhookDummy(ServerAdapter):
        server_type = ServerType.webhook
        supported_scan_modes = frozenset({ScanMode.library})

        async def trigger(self, req: ScanRequest) -> TriggerResult:
            return TriggerResult(ok=True, status_code=200, detail="ok")

        async def test(self) -> TestResult:
            return TestResult(ok=True, detail="ok")

    return _WebhookDummy


def test_register_returns_the_class_and_indexes_it(clean_registry: None) -> None:
    cls = _make_dummy_cls()
    returned = registry.register(cls)
    assert returned is cls
    assert registry.get_adapter_class(ServerType.webhook) is cls


async def test_create_adapter_instantiates_the_registered_class(
    clean_registry: None, client: httpx.AsyncClient
) -> None:
    cls = registry.register(_make_dummy_cls())
    runtime = make_plex_runtime(type=ServerType.webhook)
    adapter = registry.create_adapter(runtime, client)
    assert isinstance(adapter, cls)
    assert adapter.server is runtime
    assert adapter.client is client


def test_get_adapter_class_unknown_type_raises_value_error(clean_registry: None) -> None:
    registry._REGISTRY.pop(ServerType.emby, None)
    with pytest.raises(ValueError) as exc:
        registry.get_adapter_class(ServerType.emby)
    assert "emby" in str(exc.value)
