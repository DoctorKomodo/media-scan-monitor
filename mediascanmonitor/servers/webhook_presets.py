"""Named webhook payload presets — one entry per emitted format.

A preset is an app-owned Jinja2 body template selected by key on the webhook server row
(``Server.webhook_payload_preset``). The webhook adapter renders it with the SAME render
context as a custom template, so a preset may use every context var (``file_path``,
``server_name``, ``is_test``, ...).

Adding a format = one ``WebhookPreset`` enum member (``db/models.py``) + one entry here.
``WebhookPreset.custom`` is intentionally ABSENT: it is the "render the operator's own
``webhook_body_template``" sentinel, whose fallback is ``webhook.DEFAULT_BODY_TEMPLATE``
(kept beside the adapter, NOT duplicated here).
"""

from dataclasses import dataclass

from mediascanmonitor.db.models import WebhookPreset

# Sonarr/Radarr-compatible. subtitle-pruner reads ``file_path`` directly and short-circuits
# ``eventType == "Test"`` with a success response; ``is_test`` lets MSM's Test button send a
# recognised Test ping while real file events send "Download". ``| tojson`` keeps the JSON
# valid/escaped for paths and the literal eventType string.
_SONARR_RADARR_TEMPLATE = (
    "{\n"
    '  "eventType": {{ ("Test" if is_test else "Download") | tojson }},\n'
    '  "instanceName": {{ server_name | tojson }},\n'
    '  "file_path": {{ file_path | tojson }}\n'
    "}"
)


@dataclass(frozen=True)
class WebhookPresetDef:
    label: str  # UI display name
    body_template: str  # built-in Jinja2, rendered with the webhook render context


WEBHOOK_PRESETS: dict[WebhookPreset, WebhookPresetDef] = {
    WebhookPreset.sonarr_radarr: WebhookPresetDef(
        label="Sonarr / Radarr",
        body_template=_SONARR_RADARR_TEMPLATE,
    ),
}


def get_preset(preset: WebhookPreset) -> WebhookPresetDef:
    """Return the built-in template for a preset; raise ValueError if it has no entry."""
    try:
        return WEBHOOK_PRESETS[preset]
    except KeyError:
        raise ValueError(f"no webhook preset registered for {preset!r}") from None
