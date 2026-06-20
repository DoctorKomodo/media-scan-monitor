"""AudiobookshelfAdapter: scan URL/method/Bearer header, classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.audiobookshelf import AudiobookshelfAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://abs.example:13378"
SCAN = f"{BASE}/api/libraries/lib_abc/scan"
ME = f"{BASE}/api/me"


def abs_runtime(*, secret: str | None = "tok-secret", retry_attempts: int = 1) -> ServerRuntime:
    return make_runtime(
        type=ServerType.audiobookshelf,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="lib_abc",
        scan_key="lib:lib_abc",
    )


def test_abs_class_metadata() -> None:
    assert AudiobookshelfAdapter.server_type is ServerType.audiobookshelf
    assert AudiobookshelfAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_abs_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.audiobookshelf) is AudiobookshelfAdapter


async def test_create_adapter_builds_abs(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(abs_runtime(), client)
    assert isinstance(adapter, AudiobookshelfAdapter)
    assert adapter.client is client


@respx.mock
async def test_library_trigger_posts_scan_with_bearer_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(SCAN).mock(return_value=httpx.Response(200))
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient, status: int) -> None:
    respx.post(SCAN).mock(return_value=httpx.Response(status))
    adapter = AudiobookshelfAdapter(abs_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(SCAN).mock(side_effect=httpx.ConnectError("down"))
    adapter = AudiobookshelfAdapter(abs_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_me_with_bearer(client: httpx.AsyncClient) -> None:
    route = respx.get(ME).mock(return_value=httpx.Response(200))
    adapter = AudiobookshelfAdapter(abs_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(ME).mock(return_value=httpx.Response(401))
    adapter = AudiobookshelfAdapter(abs_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
