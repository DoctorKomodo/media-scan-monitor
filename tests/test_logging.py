"""Tests for structlog configuration and secret redaction (sub-plan 06)."""

import json
from collections.abc import Generator

import pytest
import structlog

from mediascanmonitor.observ.logging import _redact_secrets, configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None]:
    yield
    structlog.reset_defaults()


def test_configure_logging_runs_without_error() -> None:
    configure_logging(json_logs=True, level="INFO")
    log = structlog.get_logger("smoke")
    # Must not raise for any standard level call.
    log.info("started", watch_paths=3)
    log.warning("slow")
    log.error("boom", detail="x")


def test_redact_secrets_masks_sensitive_keys() -> None:
    event = {
        "event": "trigger",
        "token": "PLEX-SECRET-123",
        "Authorization": "Bearer abc",
        "api_key": "k",
        "scan_path": "/data/tv/Shoresy",
    }
    out = _redact_secrets(None, "info", event)
    assert out["token"] == "***"
    assert out["Authorization"] == "***"  # case-insensitive key match
    assert out["api_key"] == "***"
    assert out["scan_path"] == "/data/tv/Shoresy"  # non-sensitive untouched
    assert out["event"] == "trigger"


def test_json_output_redacts_secret_value(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True, level="INFO")
    structlog.get_logger("redact").info("trigger", token="PLEX-SECRET-123")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "trigger"
    assert payload["token"] == "***"
    assert "PLEX-SECRET-123" not in line


def test_level_filtering_drops_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True, level="WARNING")
    log = structlog.get_logger("filter")
    log.info("hidden")
    log.warning("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out
