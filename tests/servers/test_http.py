"""build_client + request_with_retry classification (sleep mocked so tests are instant).

Mocking approach: request_with_retry passes the module-level ``http._async_sleep`` to
tenacity's AsyncRetrying as its ``sleep`` callable, and looks that global up at call
time. Patching ``http._async_sleep`` therefore replaces the backoff sleep with an
instant no-op; we then prove retries happened by counting respx calls, not by timing.
"""

import httpx
import pytest
import respx

from mediascanmonitor.servers import http

URL = "https://backend.example/library/sections/2/refresh"


async def _instant(_seconds: float) -> None:
    return None


async def test_build_client_applies_timeout() -> None:
    client = http.build_client(verify_tls=True, timeout_seconds=7.5)
    try:
        assert isinstance(client, httpx.AsyncClient)
        assert client.timeout.connect == 7.5
        assert client.timeout.read == 7.5
    finally:
        await client.aclose()


async def test_build_client_accepts_verify_false() -> None:
    client = http.build_client(verify_tls=False, timeout_seconds=3.0)
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()


@respx.mock
async def test_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200)]
    )
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_attempts_and_returns_last_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 503
    assert route.call_count == 3


@respx.mock
async def test_does_not_retry_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_retries_transport_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200)]
    )
    async with httpx.AsyncClient() as client:
        resp = await http.request_with_retry(client, "GET", URL, attempts=3)
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_transport_error_propagates_after_exhausting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "_async_sleep", _instant)
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.TransportError):
            await http.request_with_retry(client, "GET", URL, attempts=2)
    assert route.call_count == 2
