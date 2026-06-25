"""Webhook payload-preset registry: enum/registry sync + accessor behaviour."""

import pytest

from mediascanmonitor.db.models import WebhookPreset
from mediascanmonitor.servers.webhook_presets import (
    WEBHOOK_PRESETS,
    get_preset,
)


def test_every_non_custom_preset_has_a_definition() -> None:
    # Keeps the enum and the registry in sync: every selectable format is renderable.
    for preset in WebhookPreset:
        if preset is WebhookPreset.custom:
            continue
        definition = WEBHOOK_PRESETS[preset]
        assert definition.label
        assert definition.body_template


def test_custom_is_not_in_the_registry() -> None:
    # `custom` is the "use the operator's own template" sentinel, not a built-in format.
    assert WebhookPreset.custom not in WEBHOOK_PRESETS


def test_get_preset_returns_the_definition() -> None:
    assert get_preset(WebhookPreset.sonarr_radarr).label == "Sonarr / Radarr"


def test_get_preset_rejects_an_unregistered_key() -> None:
    with pytest.raises(ValueError, match="custom"):
        get_preset(WebhookPreset.custom)
