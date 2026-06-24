"""Audiobookshelf notification adapter (contract §7).

------------------------------------------------------------------------------
AUDIOBOOKSHELF API QUIRKS (kept here so the watcher/pipeline never special-case it):

* Library scan (this endpoint is whole-library; we use library mode only):
    POST {base_url}/api/libraries/{library_id}/scan
  ABS rescans that library asynchronously. The configured ``library_id`` is the
  ABS library id (set in the UI). ``?force=1`` (force a full re-scan of unchanged
  items) is intentionally omitted — the default incremental scan is what a
  file-change notification wants. (ABS also has a path-targeted POST /api/watcher/update,
  but adopting per-folder targeting here is a deferred enhancement — see docs/FOLLOWUPS.md.)

* Auth: Authorization: Bearer {token} HEADER. Never in the URL.

* Success: ABS answers 2xx. We treat any 2xx as ok; the scan runs async.

* test(): GET {base_url}/api/me with the token proves auth + reachability.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the scan path, the /api/me
probe, the Bearer scheme, the force-flag default, and the GET /api/libraries path
(libraries[].{id,name} response shape) against current ABS API docs.
------------------------------------------------------------------------------
"""

from typing import ClassVar

import httpx
from pydantic import BaseModel

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import ScanRequest
from mediascanmonitor.servers.base import (
    LibraryListResult,
    LibraryOption,
    ServerAdapter,
    TestResult,
    TriggerResult,
)
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register


class _AbsLibrary(BaseModel):
    id: str
    name: str


class _AbsLibrariesResponse(BaseModel):
    libraries: list[_AbsLibrary]


@register
class AudiobookshelfAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.audiobookshelf
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})
    supports_library_discovery: ClassVar[bool] = True

    def _headers(self) -> dict[str, str]:
        # Bearer token in header only — never in the URL (keeps it out of logs).
        return {"Authorization": f"Bearer {self.server.secret or ''}"}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/libraries/{req.library_id}/scan"
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
                ok=True, status_code=resp.status_code, detail="Audiobookshelf scan triggered"
            )
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def test(self) -> TestResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/me"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TestResult(ok=True, detail="reachable")
        return TestResult(ok=False, detail=f"HTTP {resp.status_code}")

    async def list_libraries(self) -> LibraryListResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/api/libraries"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return LibraryListResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if not resp.is_success:
            return LibraryListResult(ok=False, detail=f"HTTP {resp.status_code}")
        try:
            parsed = _AbsLibrariesResponse.model_validate(resp.json())
        except ValueError:
            # covers httpx's json.JSONDecodeError (a ValueError) and Pydantic ValidationError.
            return LibraryListResult(ok=False, detail="unexpected response from Audiobookshelf")
        return LibraryListResult(
            ok=True,
            detail="",
            libraries=tuple(LibraryOption(id=lib.id, name=lib.name) for lib in parsed.libraries),
        )
