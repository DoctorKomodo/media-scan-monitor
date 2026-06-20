# Phase 2 — Sub-plan 03: Webhook Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the generic Webhook adapter — it relays scan events to an arbitrary HTTP endpoint with
an operator-configured method, URL, headers, and a Jinja2-templated body, self-registering into the
Phase 1 adapter registry.

**Architecture:** One self-contained module under `mediascanmonitor/servers/`, subclassing the frozen
`ServerAdapter` ABC (Phase 1 contract §7). Unlike the media-server adapters it calls no fixed API: the
HTTP method (`webhook_method`), target URL (`base_url`), headers (`webhook_headers_json`), and body
(`webhook_body_template`) all come from `ServerRuntime`. **Both header values and the body are
rendered through a Jinja2 `SandboxedEnvironment`** with a shared context, using the `| tojson` filter
for safe JSON escaping. This adapter is mode-agnostic (`supported_scan_modes = {targeted, library}`)
— it relays whatever event reaches it.

**Tech Stack:** Python 3.14, `httpx==0.28.1`, `tenacity==9.1.4` (via `servers/http.py`),
`jinja2==3.1.6` (**already pinned** for the Phase 3 web UI — reused here, no new dependency),
`respx==0.23.1`, `pytest==9.1.0` + `pytest-asyncio==1.4.0`. `ruff`/`mypy --strict` clean, line length
100. PEP 649 annotations — **no** `from __future__ import annotations`.

## Global Constraints

- **Frozen contract (Phase 1 §7), consumed not changed:** subclass `ServerAdapter`; set `server_type`
  + `supported_scan_modes`; implement `async trigger`/`async test`. Do not edit
  `servers/{base,registry,http}.py` or any contract type.
- **Security (contract invariant 3 + CLAUDE.md rule 5):**
  - The **encrypted** `secret` is exposed to the template context as `secret`, so an operator can
    inject it into an `Authorization` header (e.g. `{"Authorization": "Bearer {{ secret }}"}`)
    **without** storing the token in the plaintext `webhook_headers_json` column — the token stays
    encrypted at rest (`secret_encrypted`).
  - Never log rendered headers or the rendered body (either may contain the token).
  - Jinja2 **`SandboxedEnvironment`** blocks attribute access / template injection.
- **JSON safety:** `| tojson` is the documented way to emit valid, escaped JSON for paths containing
  quotes/backslashes. The built-in default template uses it.
- **No new dependencies** (jinja2 already present).
- Verification gate: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`.

---

## Prerequisites (hard dependency — do not start until merged)

Phase 1 **sub-plan 03** must be merged. This plan consumes, unchanged: `servers/base.py`
(`ServerAdapter`, `TriggerResult`, `TestResult`), `servers/registry.py` (`register`,
`get_adapter_class`, `create_adapter`), `servers/http.py` (`request_with_retry`),
`servers/__init__.py` (the self-registration block), and `tests/servers/conftest.py` (the generic
`make_plex_runtime` builder — its `webhook_method`/`webhook_headers_json`/`webhook_body_template`
keywords exist precisely for this adapter — plus `make_scan_request` and the `client` fixture). It
consumes the frozen types `ScanMode`/`ServerType`, `ScanRequest`/`FsEventType`, `ServerRuntime`.

This plan is **independent of sub-plans 01/02**; build in any order or in parallel. The only shared
file is `servers/__init__.py` (one appended import line).

## Webhook template-context vocabulary (Phase-2-local; defined here)

`trigger()` and `test()` render both the body and each header value with this context. Operators see
these names in the Phase 3 UI help text:

| Variable | Value |
|----------|-------|
| `event_type` | the `FsEventType` value as a string (`"created"`, `"moved_to"`, `"deleted"`, `"moved_from"`) |
| `file_path` | absolute path of the changed file |
| `host_path` | alias of `file_path` (the path on the watching host) |
| `scan_path` | computed scan directory (targeted) or `None` |
| `top_folder` | first path segment under the folder root, or `None` |
| `library_id` | backend library/section id, or `None` |
| `server_name` | the configured server name |
| `secret` | the decrypted secret (empty string if unset) — for injecting auth into a header |

> **Deferred (not in Phase 2):** `remote_path` (host→consumer path remapping). The data model carries
> no mapping field yet, so `host_path` is the only path var. Tracked in
> [`docs/FOLLOWUPS.md`](../../FOLLOWUPS.md) for Phase 3.

## File structure (what this plan builds)

| File | Responsibility |
|------|----------------|
| `mediascanmonitor/servers/webhook.py` | `WebhookAdapter` + `DEFAULT_BODY_TEMPLATE` + module-level `SandboxedEnvironment`. |
| `mediascanmonitor/servers/__init__.py` | **Modify:** append one self-registration import line. |
| `tests/servers/test_webhook.py` | Webhook unit tests (metadata, registry, method/headers/body rendering, JSON escaping, sandbox rejection, secret-in-header, error paths, `test()`). |

---

### Task 1: Webhook adapter (`servers/webhook.py`) + self-registration

**Files:**
- Create: `tests/servers/test_webhook.py`
- Create: `mediascanmonitor/servers/webhook.py`
- Modify: `mediascanmonitor/servers/__init__.py`

**Interfaces:**
- Consumes: `ServerAdapter`/`TriggerResult`/`TestResult` (`servers/base.py`); `register`,
  `get_adapter_class`, `create_adapter` (`servers/registry.py`); `request_with_retry`
  (`servers/http.py`); `ScanRequest`, `FsEventType` (`pipeline/events.py`); `ScanMode`, `ServerType`
  (`db/models.py`); `SandboxedEnvironment` + `TemplateError` (`jinja2`); `make_plex_runtime`,
  `make_scan_request`, `client` (`tests/servers/conftest.py`).
- Produces: `WebhookAdapter` (registered for `ServerType.webhook`); `DEFAULT_BODY_TEMPLATE` (the
  built-in body used when `webhook_body_template` is unset).

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_webhook.py`:

```python
"""WebhookAdapter: configurable method/headers/body, Jinja2 rendering, sandbox, test()."""

import json

import httpx
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from mediascanmonitor.servers import registry
from mediascanmonitor.servers.webhook import WebhookAdapter

from .conftest import make_plex_runtime as make_runtime
from .conftest import make_scan_request

URL = "https://hooks.example/msm"


def webhook_runtime(
    *,
    base_url: str = URL,
    secret: str | None = None,
    retry_attempts: int = 1,
    webhook_method: str | None = None,
    webhook_headers_json: str | None = None,
    webhook_body_template: str | None = None,
) -> ServerRuntime:
    return make_runtime(
        type=ServerType.webhook,
        base_url=base_url,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
        webhook_method=webhook_method,
        webhook_headers_json=webhook_headers_json,
        webhook_body_template=webhook_body_template,
    )


def library_request(
    *,
    file_path: str = "/data/media/audiobooks/Book/ch01.mp3",
    scan_path: str | None = None,
    top_folder: str | None = None,
    event_type: FsEventType = FsEventType.created,
) -> ScanRequest:
    return make_scan_request(
        scan_mode=ScanMode.library,
        scan_path=scan_path,
        library_id="5",
        scan_key="lib:5",
        file_path=file_path,
        top_folder=top_folder,
        event_type=event_type,
    )


def test_webhook_class_metadata() -> None:
    assert WebhookAdapter.server_type is ServerType.webhook
    assert WebhookAdapter.supported_scan_modes == frozenset(
        {ScanMode.targeted, ScanMode.library}
    )


def test_webhook_is_registered() -> None:
    assert registry.get_adapter_class(ServerType.webhook) is WebhookAdapter


@respx.mock
async def test_default_template_emits_valid_json(client: httpx.AsyncClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(webhook_body_template=None), client)

    # defaults: event created, scan_path None, top_folder None
    res = await adapter.trigger(library_request())

    assert res.ok is True
    assert res.status_code == 200
    body = json.loads(route.calls.last.request.content.decode())
    assert body["event"] == "created"
    assert body["scan_path"] is None  # None -> JSON null via | tojson


@respx.mock
async def test_body_tojson_escapes_special_chars(client: httpx.AsyncClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    tmpl = '{"path": {{ file_path | tojson }}}'
    adapter = WebhookAdapter(webhook_runtime(webhook_body_template=tmpl), client)
    nasty = '/tv/S01"E01\\x.mkv'  # contains a double-quote AND a backslash
    res = await adapter.trigger(library_request(file_path=nasty))
    assert res.ok is True
    body = route.calls.last.request.content.decode()
    assert json.loads(body) == {"path": nasty}  # valid JSON that round-trips


@respx.mock
async def test_method_from_config_is_honored(client: httpx.AsyncClient) -> None:
    route = respx.put(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(webhook_method="put"), client)    res = await adapter.trigger(library_request())    assert res.ok is True
    assert route.calls.last.request.method == "PUT"


@respx.mock
async def test_header_value_renders_encrypted_secret(client: httpx.AsyncClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    adapter = WebhookAdapter(
        webhook_runtime(
            secret="s3cr3t",
            webhook_headers_json='{"Authorization": "Bearer {{ secret }}"}',
        ),
        client,
    )    res = await adapter.trigger(library_request())    assert res.ok is True
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer s3cr3t"
    assert "s3cr3t" not in str(request.url)  # secret never in the URL


async def test_empty_url_is_error(client: httpx.AsyncClient) -> None:
    adapter = WebhookAdapter(webhook_runtime(base_url=""), client)    res = await adapter.trigger(library_request())    assert res.ok is False
    assert res.status_code is None
    assert "url" in res.detail.lower()


async def test_dangerous_template_is_rejected_by_sandbox(
    client: httpx.AsyncClient,
) -> None:
    # SandboxedEnvironment blocks attribute access; no HTTP request is made.
    adapter = WebhookAdapter(
        webhook_runtime(webhook_body_template="{{ ''.__class__ }}"), client
    )    res = await adapter.trigger(library_request())    assert res.ok is False
    assert res.status_code is None


async def test_invalid_headers_json_is_error(client: httpx.AsyncClient) -> None:
    adapter = WebhookAdapter(
        webhook_runtime(webhook_headers_json="not json"), client
    )    res = await adapter.trigger(library_request())    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(404))
    adapter = WebhookAdapter(webhook_runtime(), client)    res = await adapter.trigger(library_request())    assert res.ok is False
    assert res.status_code == 404


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    adapter = WebhookAdapter(webhook_runtime(retry_attempts=1), client)    res = await adapter.trigger(library_request())    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_test_sends_probe_and_reports_reachable(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(), client)    res = await adapter.test()
    assert res.ok is True
    assert route.call_count == 1


@respx.mock
async def test_test_failure_reports_status(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(500))
    adapter = WebhookAdapter(webhook_runtime(retry_attempts=1), client)    res = await adapter.test()
    assert res.ok is False
    assert "500" in res.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_webhook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.servers.webhook'`.

- [ ] **Step 3: Implement `servers/webhook.py`**

Create `mediascanmonitor/servers/webhook.py`:

```python
"""Generic webhook adapter (contract §7).

------------------------------------------------------------------------------
Relays scan events to an arbitrary HTTP endpoint. Unlike the media-server
adapters it calls no fixed API — the operator configures everything on the
Server row:

  * webhook_method        -> HTTP method (default "POST")
  * base_url              -> target URL (required)
  * webhook_headers_json  -> JSON object of header name -> value template
  * webhook_body_template -> Jinja2 body template (default: DEFAULT_BODY_TEMPLATE)

SECURITY:
  * Header VALUES and the body are rendered through a Jinja2 SandboxedEnvironment
    with the SAME context, so an operator can inject the ENCRYPTED ``secret`` into
    an Authorization header, e.g. {"Authorization": "Bearer {{ secret }}"}, WITHOUT
    storing the token in the plaintext webhook_headers_json column. The token stays
    encrypted at rest (secret_encrypted) and is never logged (we never log rendered
    headers or body).
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

from mediascanmonitor.db.models import ScanMode, ServerType
from mediascanmonitor.pipeline.events import FsEventType, ScanRequest
from mediascanmonitor.servers.base import ServerAdapter, TestResult, TriggerResult
from mediascanmonitor.servers.http import request_with_retry
from mediascanmonitor.servers.registry import register

# autoescape=False: the body is JSON/text, not HTML; ``| tojson`` does the escaping.
_ENV = SandboxedEnvironment(autoescape=False)

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

    def _context(self, req: ScanRequest) -> dict[str, Any]:
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
            "secret": self.server.secret or "",
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

    async def _send(self, req: ScanRequest) -> TriggerResult:
        url = self.server.base_url.strip()
        if not url:
            return TriggerResult(
                ok=False, status_code=None, detail="webhook url (base_url) is empty"
            )
        method = (self.server.webhook_method or "POST").upper()
        context = self._context(req)
        try:
            body = self._render(
                self.server.webhook_body_template or DEFAULT_BODY_TEMPLATE, context
            )
            headers = self._headers(context)
        except (TemplateError, ValueError, json.JSONDecodeError) as exc:
            return TriggerResult(
                ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}"
            )
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
            return TriggerResult(
                ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}"
            )
        if resp.is_success:
            return TriggerResult(
                ok=True, status_code=resp.status_code, detail="webhook delivered"
            )
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
        result = await self._send(probe)
        if result.ok:
            return TestResult(ok=True, detail="reachable")
        if result.status_code is not None:
            return TestResult(ok=False, detail=f"HTTP {result.status_code}")
        return TestResult(ok=False, detail=result.detail)
```

- [ ] **Step 4: Self-register the adapter on package import**

Modify `mediascanmonitor/servers/__init__.py` — append one line below the existing registration block:

```python
from mediascanmonitor.servers import webhook as _webhook  # noqa: F401  (registration side effect)
```

- [ ] **Step 5: Run the webhook tests to verify they pass**

Run: `pytest tests/servers/test_webhook.py -v`
Expected: PASS — 13 passed.

- [ ] **Step 6: Lint + type-check the new code**

Run: `ruff check mediascanmonitor/servers/webhook.py mediascanmonitor/servers/__init__.py tests/servers/test_webhook.py && mypy mediascanmonitor/servers/webhook.py mediascanmonitor/servers/__init__.py`
Expected: ruff "All checks passed!"; mypy "Success: no issues found".

- [ ] **Step 7: Commit**

```bash
git add tests/servers/test_webhook.py mediascanmonitor/servers/webhook.py \
        mediascanmonitor/servers/__init__.py
git commit -m "feat(servers): add generic webhook adapter with sandboxed Jinja2 templating"
```

---

### Task 2: Followups + full-suite verification gate

**Files:**
- Modify: `docs/FOLLOWUPS.md`

- [ ] **Step 1: Record the deferred `remote_path` remapping**

Modify `docs/FOLLOWUPS.md` — under the Phase 3 section, add:

```markdown
- [ ] Webhook `remote_path` template var (host→consumer path remapping) — Phase 2 exposes only
      `host_path` (no mapping field in the data model). → phase2-03 webhook plan
```

- [ ] **Step 2: Run the full gate**

Run: `ruff check . && ruff format --check . && mypy mediascanmonitor && pytest`
Expected: all green; `registry.get_adapter_class(ServerType.webhook)` resolves to `WebhookAdapter`.

- [ ] **Step 3: Commit the followups note**

```bash
git add docs/FOLLOWUPS.md
git commit -m "docs(followups): defer webhook remote_path remapping to Phase 3"
```

---

## Self-Review

**Spec coverage:**

- Configurable method/URL/headers/body from `ServerRuntime` — Task 1 implementation `_send`. ✓
- Jinja2 `SandboxedEnvironment` body + header rendering — `_render`/`_headers`;
  `test_dangerous_template_is_rejected_by_sandbox`. ✓
- `| tojson` JSON-escaping correctness with quotes + backslashes —
  `test_body_tojson_escapes_special_chars`. ✓
- Default body template emits valid JSON (None → null) — `test_default_template_emits_valid_json`. ✓
- Encrypted `secret` injectable into a header, never in URL — `test_header_value_renders_encrypted_secret`. ✓
- Mode-agnostic (`supported_scan_modes == {targeted, library}`) — `test_webhook_class_metadata`. ✓
- Self-registration — `test_webhook_is_registered`. ✓
- Error paths: empty URL, bad headers JSON, HTTP error, transport error — dedicated tests. ✓
- `test()` sends a synthetic probe; 2xx → reachable, else reports status —
  `test_test_sends_probe_*` / `test_test_failure_reports_status`. ✓
- `remote_path` deferred + recorded in FOLLOWUPS — Task 2. ✓
- No new dependency (jinja2 already pinned); frozen contract untouched. ✓

**Placeholder scan:** none — complete code + commands throughout.

**Type consistency:** `WebhookAdapter` sets `server_type`/`supported_scan_modes` and implements
`trigger(self, req: ScanRequest) -> TriggerResult` / `test(self) -> TestResult` per the frozen ABC.
`_context`/`_render`/`_headers`/`_send` are private helpers; `request_with_retry` call (with
`content=` and `headers=`) matches its `**kwargs` contract signature. `DEFAULT_BODY_TEMPLATE` is the
single source of the built-in body.

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-06-20-phase2-03-webhook-adapter.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session using `executing-plans`, batched with checkpoints.

Which approach?
