"""Structured (structlog) logging configuration.

`configure_logging` is called once at process start. It installs a small,
fixed processor chain that renders either JSON (production/Docker) or a
human-friendly console format, and a redaction processor that masks values
for a fixed set of sensitive keys so secrets never reach the log sink
(CLAUDE rule 5: "never log secrets").
"""

import logging

import structlog
from structlog.typing import EventDict, WrappedLogger

# Keys whose VALUES must never be emitted. Matched case-insensitively.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"token", "secret", "password", "api_key", "authorization", "x_plex_token"}
)
_REDACTED = "***"


def _redact_secrets(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: mask values for known sensitive keys in place."""
    for key in list(event_dict):
        if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, json_logs: bool = True, level: str = "INFO") -> None:
    """Configure structlog process-wide.

    Args:
        json_logs: emit one JSON object per line when True; otherwise a
            colored console format for local development.
        level: minimum level name ("DEBUG", "INFO", "WARNING", "ERROR",
            "CRITICAL"); unknown names fall back to INFO.
    """
    level_no = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
