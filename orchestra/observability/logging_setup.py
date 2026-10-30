"""Structured logging configuration with contextvars trace binding and secret redaction (§29.1)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from orchestra.security.secrets import DEFAULT_REDACTOR, SecretRedactor


def configure_logging(
    *,
    json_output: bool = True,
    redactor: SecretRedactor | None = None,
) -> None:
    """Configure `structlog` pipeline with contextvars merge, timestamps, and INV-07 redaction."""
    active_redactor = redactor or DEFAULT_REDACTOR
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            active_redactor.structlog_processor,
            renderer,
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "orchestra") -> Any:
    """Return a configured `structlog` logger bound to `component=name`."""
    return structlog.get_logger().bind(component=name)


@contextmanager
def bind_trace_context(**fields: Any) -> Iterator[None]:
    """Bind correlation fields (`run_id`, `node_id`, `agent_id`, `key_id`, `trace_id`) in scope."""
    tokens = structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
