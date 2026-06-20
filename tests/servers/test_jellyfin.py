"""JellyfinAdapter: MediaBrowser auth format, mandatory refresh query params, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.jellyfin import JellyfinAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

BASE = "https://jellyfin.example:8096"
REFRESH = f"{BASE}/Items/7/Refresh"
INFO = f"{BASE}/System/Info"


def jf_runtime(*, secret: str | None = "tok-secret", retry_attempts: int = 1) -> ServerRuntime:
    return make_runtime(
        type=ServerType.jellyfin,
        base_url=BASE,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
    )


def library_request() -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library, scan_path=None, library_id="7", scan_key="lib:7"
    )


def test_jellyfin_class_metadata() -> None:
    assert JellyfinAdapter.server_type is ServerType.jellyfin
    assert JellyfinAdapter.supported_scan_modes == frozenset({ScanMode.library})


def test_jellyfin_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.jellyfin) is JellyfinAdapter


@respx.mock
async def test_library_trigger_sends_refresh_modes_and_mediabrowser_auth(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(REFRESH).mock(return_value=httpx.Response(204))
    adapter = JellyfinAdapter(jf_runtime(secret="tok-secret"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
    assert res.status_code == 204
    request = route.calls.last.request
    assert request.method == "POST"
    # Jellyfin requires all three query params on a recursive refresh
    assert request.url.params["Recursive"] == "true"
    assert request.url.params["metadataRefreshMode"] == "Default"
    assert request.url.params["imageRefreshMode"] == "Default"
    # exact MediaBrowser auth header format; token never in the URL
    assert request.headers["Authorization"] == 'MediaBrowser Token="tok-secret"'
    assert "tok-secret" not in str(request.url)


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient, status: int) -> None:
    respx.post(REFRESH).mock(return_value=httpx.Response(status))
    adapter = JellyfinAdapter(jf_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = JellyfinAdapter(jf_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_test_happy_path_hits_system_info_with_mediabrowser_auth(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(INFO).mock(return_value=httpx.Response(200))
    adapter = JellyfinAdapter(jf_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.headers["Authorization"] == 'MediaBrowser Token="tok-secret"'
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(INFO).mock(return_value=httpx.Response(401))
    adapter = JellyfinAdapter(jf_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail
