"""Crypto skill: table-driven order state machine including mandatory `UNKNOWN` (§38.3, DP-07)."""

from __future__ import annotations

import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "order_state_machine",
    "name": "Order Lifecycle State Machine Validator",
    "version": "1.0.0",
    "description": "Validate deterministic order state transitions including UNKNOWN->RECONCILED.",
    "tags": ("crypto", "orders", "fsm", "execution"),
    "input_schema": {
        "type": "object",
        "required": ["current", "event"],
        "properties": {"current": {"type": "string"}, "event": {"type": "string"}},
    },
    "output_schema": {"type": "object", "required": ["next", "legal"]},
}

_TRANSITIONS: dict[tuple[str, str], str] = {
    ("NEW", "SUBMIT"): "SUBMITTED",
    ("NEW", "LINK_LOST"): "UNKNOWN",
    ("SUBMITTED", "ACK"): "ACCEPTED",
    ("SUBMITTED", "REJECT"): "REJECTED",
    ("SUBMITTED", "TIMEOUT"): "UNKNOWN",
    ("SUBMITTED", "LINK_LOST"): "UNKNOWN",
    ("ACCEPTED", "PARTIAL_FILL"): "PARTIALLY_FILLED",
    ("ACCEPTED", "FILL"): "FILLED",
    ("ACCEPTED", "CANCEL"): "CANCELLED",
    ("ACCEPTED", "EXPIRE"): "EXPIRED",
    ("ACCEPTED", "LINK_LOST"): "UNKNOWN",
    ("PARTIALLY_FILLED", "PARTIAL_FILL"): "PARTIALLY_FILLED",
    ("PARTIALLY_FILLED", "FILL"): "FILLED",
    ("PARTIALLY_FILLED", "CANCEL"): "CANCELLED",
    ("PARTIALLY_FILLED", "LINK_LOST"): "UNKNOWN",
    ("UNKNOWN", "RECONCILE"): "RECONCILED",
    ("RECONCILED", "RESOLVE_ACCEPTED"): "ACCEPTED",
    ("RECONCILED", "RESOLVE_PARTIAL"): "PARTIALLY_FILLED",
    ("RECONCILED", "RESOLVE_FILLED"): "FILLED",
    ("RECONCILED", "RESOLVE_CANCELLED"): "CANCELLED",
    ("RECONCILED", "RESOLVE_NOT_FOUND"): "REJECTED",
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate `(current, event)` against canonical order transition table (DP-07..10)."""
    cur = str(payload.get("current", "")).strip().upper()
    ev = str(payload.get("event", "")).strip().upper()
    if not cur or not ev:
        raise ValueError("current and event are required")
    nxt = _TRANSITIONS.get((cur, ev))
    if nxt is None:
        return {"next": cur, "legal": False}
    return {"next": nxt, "legal": True}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
