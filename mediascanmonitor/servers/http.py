"""Shared httpx client construction and a tenacity-based retry helper (contract §7).

Retry policy: retry on transport errors (DNS/connect/read failures) and on 5xx
responses; never retry 4xx. Exponential backoff between attempts. The sleep used
between attempts is the module-level ``_async_sleep`` so tests can patch it to a
no-op (see tests/servers/test_http.py).
"""

import asyncio
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def build_client(*, verify_tls: bool, timeout_seconds: float) -> httpx.AsyncClient:
    """Construct the shared async client (one per server, owned by the engine)."""
    return httpx.AsyncClient(verify=verify_tls, timeout=timeout_seconds)


async def _async_sleep(seconds: float) -> None:  # pragma: no cover - patched in tests
    """Backoff sleep; patched to a no-op in tests via monkeypatch on this attribute."""
    await asyncio.sleep(seconds)


class _RetryableStatus(Exception):
    """Internal marker carrying a 5xx response so tenacity can retry on it."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"server error {response.status_code}")
        self.response = response


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TransportError, _RetryableStatus))


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int,
    **kwargs: Any,
) -> httpx.Response:
    """Issue ``method url`` with retry on transport errors + 5xx (exp backoff).

    On give-up after a 5xx, returns the last response (so callers classify it as a
    failure). On give-up after a transport error, the httpx exception propagates.
    4xx responses are returned immediately without retry.
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=0.2, max=5.0),
        retry=retry_if_exception(_is_retryable),
        sleep=_async_sleep,
        reraise=True,
    )
    try:
        async for attempt in retrying:
            with attempt:
                response = await client.request(method, url, **kwargs)
                if 500 <= response.status_code < 600:
                    raise _RetryableStatus(response)
                return response
    except _RetryableStatus as exc:
        return exc.response
    raise AssertionError("unreachable")  # pragma: no cover
