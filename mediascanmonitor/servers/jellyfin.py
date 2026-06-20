"""Jellyfin notification adapter (contract §7).

------------------------------------------------------------------------------
JELLYFIN API QUIRKS (kept here so the watcher/pipeline never special-case it):

* Library refresh (no native path targeting — library mode only):
    POST {base_url}/Items/{library_id}/Refresh
        ?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default
  All three query params are required for a recursive refresh. The configured
  ``library_id`` is the collection-folder id (Phase 3's UI will help find it via
  GET /Library/VirtualFolders; the adapter takes it as given).

* Auth: Authorization: MediaBrowser Token="{token}" HEADER (note the literal
  ``MediaBrowser`` scheme and the DOUBLE-QUOTED token). Never in the URL.

* Success: Jellyfin answers 2xx (usually 204). We treat any 2xx as ok.

* test(): GET {base_url}/System/Info with the token proves auth + reachability.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the Refresh path + query
params, the System/Info probe, and the MediaBrowser auth header format against
current Jellyfin API docs.
------------------------------------------------------------------------------
"""

from typing import ClassVar

import httpx

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


@register
class JellyfinAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.jellyfin
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})

    def _headers(self) -> dict[str, str]:
        # MediaBrowser scheme, double-quoted token; header only, never in the URL.
        return {"Authorization": f'MediaBrowser Token="{self.server.secret or ""}"'}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = (
            f"{base}/Items/{req.library_id}/Refresh"
            "?Recursive=true&metadataRefreshMode=Default&imageRefreshMode=Default"
        )
        try:
            resp = await request_with_retry(
                self.client,
                "POST",
                url,
                attempts=self.server.retry_attempts,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TriggerResult(
                ok=True, status_code=resp.status_code, detail="Jellyfin refresh triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/System/Info"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TestResult(ok=True, detail="reachable")
        return TestResult(ok=False, detail=f"HTTP {resp.status_code}")
