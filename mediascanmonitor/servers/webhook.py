"""Generic webhook adapter (contract §7).

------------------------------------------------------------------------------
Relays scan events to an arbitrary HTTP endpoint. Unlike the media-server
adapters it calls no fixed API — the operator configures everything on the
Server row:

  * webhook_method        -> HTTP method (default "POST")
  * base_url              -> target URL (required)
  * webhook_headers_json  -> JSON object of header name -> value template
  * webhook_body_template -> Jinja2 body template (default: DEFAULT_BODY_TEMPLATE)
  * webhook_payload_preset -> named app-managed payload (WebhookPreset). "custom"
                              renders webhook_body_template (above); any other value
                              renders a built-in template from servers/webhook_presets.py
                              (e.g. "sonarr_radarr" — a subtitle-pruner-compatible payload).
                              When a preset is active, webhook_body_template is ignored.

SECURITY:
  * Header VALUES and the body are rendered through a Jinja2 SandboxedEnvironment
    with the SAME context, so an operator can inject the ``secret`` into an
    Authorization header, e.g. {"Authorization": "Bearer {{ secret }}"}, WITHOUT
    storing the token in the plaintext webhook_headers_json column. The token stays
    encrypted at rest (secret_encrypted) — decrypted only in memory at render time —
    and is never logged (we never log rendered headers or body).
  * SandboxedEnvironment blocks attribute access / template injection.
  * ``| tojson`` emits valid, escaped JSON for paths with quotes/backslashes.

test(): renders + sends the configured request with a synthetic test event (a real
webhook has no generic "ping"); any 2xx => reachable.
------------------------------------------------------------------------------
"""

import json
from typing import Any, ClassVar

import httpx
from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from mediascanmonitor import APP_NAME
from mediascanmonitor.db.models import ScanMode, ServerType, WebhookPreset
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register
from mediascanmonitor.servers.webhook_presets import get_preset

# autoescape=False: the body is JSON/text, not HTML; ``| tojson`` does the escaping.
_ENV = SandboxedEnvironment(autoescape=False)

# HTTP methods offered in the UI's webhook Method <select>. POST is the default and by far
# the common case; the rest cover the occasional REST-style endpoint. The adapter itself
# accepts any verb (it just uppercases webhook_method), so this list only bounds the UI.
HTTP_METHODS: tuple[str, ...] = ("POST", "PUT", "PATCH", "GET", "DELETE")

DEFAULT_BODY_TEMPLATE = (
    "{\n"
    '  "event": {{ event_type | tojson }},\n'
    '  "file_path": {{ file_path | tojson }},\n'
    '  "scan_path": {{ scan_path | tojson }},\n'
    '  "top_folder": {{ top_folder | tojson }},\n'
    '  "library_id": {{ library_id | tojson }},\n'
    '  "server": {{ server_name | tojson }}\n'
    "}"
)


@register
class WebhookAdapter(ServerAdapter):
    server_type: ClassVar[ServerType] = ServerType.webhook
    supported_scan_modes: ClassVar[frozenset[ScanMode]] = frozenset(
        {ScanMode.targeted, ScanMode.library}
    )

    def _context(self, req: ScanRequest, *, is_test: bool = False) -> dict[str, Any]:
        return {
            # Use .value for the bare event name ("created"). FsEventType is a
            # StrEnum, so str(member) is also "created" — .value is explicit and
            # stays correct regardless of the enum base.
            "event_type": req.event_type.value,
            "file_path": req.file_path,
            "host_path": req.file_path,
            "scan_path": req.scan_path,
            "top_folder": req.top_folder,
            "library_id": req.library_id,
            "server_name": self.server.name,
            # MSM's own identity (the caller), distinct from server_name (the user's label
            # for this target). Presets use it so receivers log MSM, not the target's name.
            "app_name": APP_NAME,
            "secret": self.server.secret or "",
            "is_test": is_test,
        }

    def _render(self, template: str, context: dict[str, Any]) -> str:
        return _ENV.from_string(template).render(**context)

    def _headers(self, context: dict[str, Any]) -> dict[str, str]:
        raw = json.loads(self.server.webhook_headers_json or "{}")
        if not isinstance(raw, dict):
            raise ValueError("webhook_headers_json must be a JSON object")
        headers = {"Content-Type": "application/json"}
        for key, value in raw.items():
            headers[str(key)] = self._render(str(value), context)
        return headers

    async def _send(self, req: ScanRequest, *, is_test: bool = False) -> TriggerResult:
        url = self.server.base_url.strip()
        if not url:
            return TriggerResult(
                ok=False, status_code=None, detail="webhook url (base_url) is empty"
            )
        method = (self.server.webhook_method or "POST").upper()
        context = self._context(req, is_test=is_test)
        preset = self.server.webhook_payload_preset
        if preset == WebhookPreset.custom:
            template = self.server.webhook_body_template or DEFAULT_BODY_TEMPLATE
        else:
            template = get_preset(preset).body_template
        try:
            body = self._render(template, context)
            headers = self._headers(context)
        except (TemplateError, ValueError, json.JSONDecodeError) as exc:
            return TriggerResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")
        try:
            resp = await request_with_retry(
                self.client,
                method,
                url,
                attempts=self.server.retry_attempts,
                headers=headers,
                content=body.encode("utf-8"),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TriggerResult(ok=True, status_code=resp.status_code, detail="webhook delivered")
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )

    async def trigger(self, req: ScanRequest) -> TriggerResult:
        return await self._send(req)

    async def test(self) -> TestResult:
        probe = ScanRequest(
            server_id=self.server.server_id,
            server_name=self.server.name,
            scan_mode=self.server.scan_mode,
            scan_path=None,
            library_id=None,
            scan_key="test",
            event_type=FsEventType.created,
            file_path="/__msm_test__",
            top_folder=None,
        )
        result = await self._send(probe, is_test=True)
        if result.ok:
            return TestResult(ok=True, detail="reachable")
        if result.status_code is not None:
            return TestResult(ok=False, detail=f"HTTP {result.status_code}")
        return TestResult(ok=False, detail=result.detail)
