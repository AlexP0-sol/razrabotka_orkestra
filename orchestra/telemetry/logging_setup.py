"""Canonical §10 telemetry logging setup module delegating to `observability.logging_setup`."""

from __future__ import annotations

from orchestra.observability.logging_setup import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
