"""Emby notification adapter (contract §7).

------------------------------------------------------------------------------
EMBY API QUIRKS (kept here so the watcher/pipeline never special-case Emby):

* Library refresh (no native path targeting — library mode only):
    POST {base_url}/Items/{library_id}/Refresh?Recursive=true
  Emby refreshes that library item and its descendants asynchronously. The
  configured ``library_id`` is the library/collection item id (set in the UI).

* Auth: the token goes in the ``X-Emby-Token`` HEADER. Never put it in the URL
  so it cannot leak into logs.

* Success: Emby answers 2xx (usually 204, empty body). We treat any 2xx as ok;
  there is no per-item completion signal to await.

* test(): GET {base_url}/System/Info with the token proves auth + reachability.

* Library discovery:
    GET {base_url}/Library/VirtualFolders
  Returns a TOP-LEVEL JSON array of VirtualFolderInfo; each entry's ``ItemId`` is
  the library/collection item id (the same value used in the Refresh URL above)
  and ``Name`` is the label.

VERIFY AT IMPLEMENT-TIME (CLAUDE.md rule 1): confirm the Refresh path, the
System/Info probe, the X-Emby-Token header, and the /Library/VirtualFolders
response shape (array of {Name, ItemId}) against current Emby API docs.
------------------------------------------------------------------------------
"""

from typing import ClassVar

import httpx
from pydantic import BaseModel, Field, TypeAdapter

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


class _EmbyVirtualFolder(BaseModel):
    item_id: str = Field(alias="ItemId")
    name: str = Field(alias="Name")


# /Library/VirtualFolders is a bare JSON array, not an object — validate the list directly.
_EMBY_VFOLDERS = TypeAdapter(list[_EmbyVirtualFolder])


@register
class EmbyAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.emby
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset({ScanMode.library})
    supports_library_discovery: ClassVar[bool] = True

    def _headers(self) -> dict[str, str]:
        # Token in header only — never in the URL (keeps it out of logs).
        return {"X-Emby-Token": self.server.secret or ""}

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/Items/{req.library_id}/Refresh?Recursive=true"
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
                ok=True, status_code=resp.status_code, detail="Emby refresh triggered"
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

    async def list_libraries(self) -> LibraryListResult:
        base = self.server.base_url.rstrip("/")
        url = f"{base}/Library/VirtualFolders"
        try:
            resp = await request_with_retry(
                self.client, "GET", url, attempts=1, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return LibraryListResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        if not resp.is_success:
            return LibraryListResult(ok=False, detail=f"HTTP {resp.status_code}")
        try:
            folders = _EMBY_VFOLDERS.validate_python(resp.json())
        except ValueError:
            # covers httpx's json.JSONDecodeError (a ValueError) and Pydantic ValidationError.
            return LibraryListResult(ok=False, detail="unexpected response from Emby")
        return LibraryListResult(
            ok=True,
            detail="",
            libraries=tuple(LibraryOption(id=f.item_id, name=f.name) for f in folders),
        )
