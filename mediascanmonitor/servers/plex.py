"""Plex notification adapter (contract §7).

------------------------------------------------------------------------------
PLEX API QUIRKS (kept here so the watcher/pipeline never special-case Plex):

* Partial (targeted) scan:
    GET {base_url}/library/sections/{library_id}/refresh?path={url-encoded path}
  Plex matches ``path`` against the on-disk library path and rescans only that
  subtree. We URL-encode with ``safe="/"`` so the path separators stay literal
  (Plex expects a real path, not a fully percent-escaped blob); spaces become
  %20, ampersands %26, etc.

* Whole-library scan: the same URL WITHOUT ``?path=``.

* Auth: the token goes in the ``X-Plex-Token`` HEADER. Plex also accepts it as a
  query param, but we never put it in the URL so it cannot leak into logs.

* Success: Plex answers 2xx (usually 200, empty body) and scans asynchronously.
  We treat any 2xx as ok; there is no per-item completion signal to await.
------------------------------------------------------------------------------
"""

from typing import ClassVar
from urllib.parse import quote

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


@register
class PlexAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.plex
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset(
        {ScanMode.targeted, ScanMode.library}
    )

    def _headers(self) -> dict[str, str]:
        # Token in header only — never in the URL (keeps it out of logs).
        return {"X-Plex-Token": self.server.secret or ""}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/library/sections/{req.library_id}/refresh"
        if req.scan_mode is ScanMode.targeted and req.scan_path is not None:
            url = f"{url}?path={quote(req.scan_path, safe='/')}"
        try:
            resp = await request_with_retry(
                self.client,
                "GET",
                url,
                attempts=self.server.retry_attempts,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(
                ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}"
            )
        if resp.is_success:
            return TriggerResult(
                ok=True, status_code=resp.status_code, detail="Plex scan triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/identity"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TestResult(ok=True, detail="reachable")
        return TestResult(ok=False, detail=f"HTTP {resp.status_code}")
