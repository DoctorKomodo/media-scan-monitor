"""EmbyAdapter: exact URL/method/header, success/failure classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.emby import EmbyAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://emby.example:8096"
REFRESH = f"{BASE}/Items/5/Refresh"
INFO = f"{BASE}/System/Info"


def emby_runtime(*, secret: str | None = "tok-secret", retry_attempts: int = 1) -> ServerRuntime:
    return make_runtime(
        type=ServerType.emby,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library, scan_path=None, library_id="5", scan_key="lib:5"
    )


def test_emby_class_metadata() -> None:
    assert EmbyAdapter.server_type is ServerType.emby
    assert EmbyAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_emby_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.emby) is EmbyAdapter


async def test_create_adapter_builds_emby(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(emby_runtime(), client)
    assert isinstance(adapter, EmbyAdapter)
    assert adapter.client is client


@respx.mock
async def test_library_trigger_posts_recursive_with_token_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(REFRESH).mock(return_value=httpx.Response(200))
    adapter = EmbyAdapter(emby_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.params["Recursive"] == "true"
    # token in the HEADER, never in the URL
    assert request.headers["X-Emby-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient, status: int) -> None:
    respx.post(REFRESH).mock(return_value=httpx.Response(status))
    adapter = EmbyAdapter(emby_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = EmbyAdapter(emby_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_system_info_with_token(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(INFO).mock(return_value=httpx.Response(200))
    adapter = EmbyAdapter(emby_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["X-Emby-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(INFO).mock(return_value=httpx.Response(401))
    adapter = EmbyAdapter(emby_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
