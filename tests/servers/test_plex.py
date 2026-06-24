"""PlexAdapter: exact URL/encoding/header, success/failure classification, test()."""

import httpx
import pytest
import respx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.plex import PlexAdapter

from .conftest import make_plex_runtime, make_scan_request

BASE = "https://plex.example:32400"
REFRESH = f"{BASE}/library/sections/2/refresh"
IDENTITY = f"{BASE}/identity"
SECTIONS = f"{BASE}/library/sections"


def test_plex_class_metadata() -> None:
    assert PlexAdapter.server_type is ServerType.plex
    assert PlexAdapter.supported_scan_modes == frozenset({ScanMode.targeted, ScanMode.library})


def test_plex_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.plex) is PlexAdapter


async def test_create_adapter_builds_plex(client: httpx.AsyncClient) -> None:
    adapter = registry.create_adapter(make_plex_runtime(), client)
    assert isinstance(adapter, PlexAdapter)
    assert adapter.client is client


@respx.mock
async def test_targeted_trigger_encodes_path_and_sends_token_in_header(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(secret="tok-secret"), client)
    req = make_scan_request(
        scan_mode=ScanMode.targeted,
        scan_path="/data/media/tvseries/Tom & Jerry",
        library_id="2",
    )

    res = await adapter.trigger(req)

    assert res.ok is True
    assert res.status_code == 200
    assert route.call_count == 1

    request = route.calls.last.request
    assert request.method == "GET"
    # token is in the HEADER, never in the URL/query
    assert request.headers["X-Plex-Token"] == "tok-secret"
    assert "X-Plex-Token" not in str(request.url)
    assert "tok-secret" not in str(request.url)
    # path query param: decoded round-trips, and the raw URL shows %20 and %26 encoding
    assert request.url.params["path"] == "/data/media/tvseries/Tom & Jerry"
    assert "path=/data/media/tvseries/Tom%20%26%20Jerry" in str(request.url)


@respx.mock
async def test_library_trigger_has_no_path_param(client: httpx.AsyncClient) -> None:
    route = respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(), client)
    req = make_scan_request(
        scan_mode=ScanMode.library,
        scan_path=None,
        library_id="2",
        scan_key="lib:2",
    )

    res = await adapter.trigger(req)

    assert res.ok is True
    assert res.status_code == 200
    request = route.calls.last.request
    assert "path" not in request.url.params
    assert request.url.query == b""
    assert request.headers["X-Plex-Token"] == "tok-secret"


@respx.mock
async def test_trigger_success_classification(client: httpx.AsyncClient) -> None:
    respx.get(REFRESH).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.trigger(make_scan_request())
    assert res == res.__class__(ok=True, status_code=200, detail=res.detail)
    assert res.ok is True and res.status_code == 200


@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient, status: int) -> None:
    respx.get(REFRESH).mock(return_value=httpx.Response(status))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.trigger(make_scan_request())
    assert res.ok is False
    assert res.status_code == status


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(REFRESH).mock(side_effect=httpx.ConnectError("down"))
    adapter = PlexAdapter(make_plex_runtime(retry_attempts=1), client)
    res = await adapter.trigger(make_scan_request())
    assert res.ok is False
    assert res.status_code is None
    assert "down" in res.detail or "ConnectError" in res.detail


@respx.mock
async def test_test_happy_path_hits_identity_with_token(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(IDENTITY).mock(return_value=httpx.Response(200))
    adapter = PlexAdapter(make_plex_runtime(secret="tok-secret"), client)
    res = await adapter.test()
    assert res.ok is True
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["X-Plex-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_test_auth_failure_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.get(IDENTITY).mock(return_value=httpx.Response(401))
    adapter = PlexAdapter(make_plex_runtime(), client)
    res = await adapter.test()
    assert res.ok is False
    assert "401" in res.detail


def test_plex_supports_library_discovery() -> None:
    assert PlexAdapter.supports_library_discovery is True


@respx.mock
async def test_list_libraries_parses_key_and_title(client: httpx.AsyncClient) -> None:
    respx.get(SECTIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "size": 2,
                    "Directory": [
                        {"key": "2", "title": "Movies", "type": "movie"},
                        {"key": "5", "title": "TV Shows", "type": "show"},
                    ],
                }
            },
        )
    )
    adapter = PlexAdapter(make_plex_runtime(secret="tok"), client)
    result = await adapter.list_libraries()
    assert result.ok is True
    assert [(o.id, o.name) for o in result.libraries] == [
        ("2", "Movies"),
        ("5", "TV Shows"),
    ]


@respx.mock
async def test_list_libraries_sends_token_header_and_accept_json(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get(SECTIONS).mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Directory": []}})
    )
    adapter = PlexAdapter(make_plex_runtime(secret="tok-secret"), client)
    await adapter.list_libraries()
    request = route.calls.last.request
    # Plex defaults to XML; we must ask for JSON.
    assert request.headers["Accept"] == "application/json"
    # token in header only, never in the URL.
    assert request.headers["X-Plex-Token"] == "tok-secret"
    assert "tok-secret" not in str(request.url)


@respx.mock
async def test_list_libraries_empty_server_has_no_directory_key(
    client: httpx.AsyncClient,
) -> None:
    # A server with no libraries omits Directory entirely.
    respx.get(SECTIONS).mock(return_value=httpx.Response(200, json={"MediaContainer": {"size": 0}}))
    result = await PlexAdapter(make_plex_runtime(), client).list_libraries()
    assert result.ok is True
    assert result.libraries == ()


@respx.mock
async def test_list_libraries_maps_401_to_error(client: httpx.AsyncClient) -> None:
    respx.get(SECTIONS).mock(return_value=httpx.Response(401))
    result = await PlexAdapter(make_plex_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail == "HTTP 401"
    assert result.libraries == ()


@respx.mock
async def test_list_libraries_maps_connection_error(client: httpx.AsyncClient) -> None:
    respx.get(SECTIONS).mock(side_effect=httpx.ConnectError("boom"))
    result = await PlexAdapter(make_plex_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail.startswith("ConnectError")


@respx.mock
async def test_list_libraries_maps_garbage_body(client: httpx.AsyncClient) -> None:
    respx.get(SECTIONS).mock(return_value=httpx.Response(200, text="<xml>not json</xml>"))
    result = await PlexAdapter(make_plex_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail == "unexpected response from Plex"


@respx.mock
async def test_list_libraries_maps_missing_media_container(client: httpx.AsyncClient) -> None:
    # Valid JSON, but the MediaContainer envelope is absent (e.g. an error body).
    respx.get(SECTIONS).mock(return_value=httpx.Response(200, json={}))
    result = await PlexAdapter(make_plex_runtime(), client).list_libraries()
    assert result.ok is False
    assert result.detail == "unexpected response from Plex"
