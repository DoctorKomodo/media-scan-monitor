"""WebhookAdapter: configurable method/headers/body, Jinja2 rendering, sandbox, test()."""

import json

import httpx
import respx

from mediascanmonitor.config.runtime import ServerRuntime
from mediascanmonitor.db.models import ScanMode, ServerType, WebhookPreset
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
    webhook_payload_preset: WebhookPreset = WebhookPreset.custom,
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
        webhook_payload_preset=webhook_payload_preset,
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
    assert WebhookAdapter.supported_scan_modes == frozenset({ScanMode.targeted, ScanMode.library})


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
    adapter = WebhookAdapter(webhook_runtime(webhook_method="put"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is True
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
    )
    res = await adapter.trigger(library_request())
    assert res.ok is True
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer s3cr3t"
    assert "s3cr3t" not in str(request.url)  # secret never in the URL


@respx.mock
async def test_empty_url_is_error(client: httpx.AsyncClient) -> None:
    adapter = WebhookAdapter(webhook_runtime(base_url=""), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None
    assert "url" in res.detail.lower()


@respx.mock
async def test_dangerous_template_is_rejected_by_sandbox(
    client: httpx.AsyncClient,
) -> None:
    # A real sandbox-escape probe: SandboxedEnvironment raises SecurityError (a
    # TemplateError) when the template walks into class internals, so trigger()
    # returns ok=False BEFORE any HTTP call. @respx.mock registers no route, so if
    # rendering ever stopped raising and a request escaped, this test would fail
    # with AllMockedAssertionError instead of passing silently.
    adapter = WebhookAdapter(
        webhook_runtime(webhook_body_template="{{ ''.__class__.__mro__[1].__subclasses__() }}"),
        client,
    )
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_invalid_headers_json_is_error(client: httpx.AsyncClient) -> None:
    adapter = WebhookAdapter(webhook_runtime(webhook_headers_json="not json"), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_trigger_http_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(404))
    adapter = WebhookAdapter(webhook_runtime(), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code == 404


@respx.mock
async def test_trigger_transport_error_is_not_ok(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    adapter = WebhookAdapter(webhook_runtime(retry_attempts=1), client)
    res = await adapter.trigger(library_request())
    assert res.ok is False
    assert res.status_code is None


@respx.mock
async def test_test_sends_probe_and_reports_reachable(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(), client)
    res = await adapter.test()
    assert res.ok is True
    assert route.call_count == 1


@respx.mock
async def test_test_failure_reports_status(client: httpx.AsyncClient) -> None:
    respx.post(URL).mock(return_value=httpx.Response(500))
    adapter = WebhookAdapter(webhook_runtime(retry_attempts=1), client)
    res = await adapter.test()
    assert res.ok is False
    assert "500" in res.detail


@respx.mock
async def test_sonarr_radarr_preset_emits_download_and_path(client: httpx.AsyncClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(webhook_payload_preset=WebhookPreset.sonarr_radarr), client
    )
    res = await adapter.trigger(library_request(file_path="/data/tv/Show/ep.mkv"))
    assert res.ok is True
    body = json.loads(route.calls.last.request.content.decode())
    assert body == {
        "eventType": "Download",
        "instanceName": "My Plex",  # make_plex_runtime default name
        "file_path": "/data/tv/Show/ep.mkv",
    }


@respx.mock
async def test_sonarr_radarr_preset_ignores_custom_body_template(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(
            webhook_payload_preset=WebhookPreset.sonarr_radarr,
            webhook_body_template='{"ignored": true}',
        ),
        client,
    )
    await adapter.trigger(library_request())
    body = json.loads(route.calls.last.request.content.decode())
    assert "ignored" not in body
    assert body["eventType"] == "Download"


@respx.mock
async def test_sonarr_radarr_preset_test_button_announces_test_event(
    client: httpx.AsyncClient,
) -> None:
    # subtitle-pruner short-circuits eventType == "Test", so the Test button must send it.
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(webhook_payload_preset=WebhookPreset.sonarr_radarr), client
    )
    res = await adapter.test()
    assert res.ok is True
    body = json.loads(route.calls.last.request.content.decode())
    assert body["eventType"] == "Test"


@respx.mock
async def test_custom_preset_still_renders_default_template(client: httpx.AsyncClient) -> None:
    # The default (custom) preset is unchanged: DEFAULT_BODY_TEMPLATE shape with an "event" key.
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(), client)  # preset defaults to custom
    await adapter.trigger(library_request())
    body = json.loads(route.calls.last.request.content.decode())
    assert body["event"] == "created"
