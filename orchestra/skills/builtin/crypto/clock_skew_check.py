"""Crypto skill: RTT-compensated exchange clock skew monitor (DP-17, DP-18, TR-18)."""

from __future__ import annotations

import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "clock_skew_check",
    "name": "Exchange Server Clock Skew Checker",
    "version": "1.0.0",
    "description": "Compute RTT/2-adjusted clock skew in ms and check against 1000ms threshold.",
    "tags": ("crypto", "clock", "skew", "time"),
    "input_schema": {
        "type": "object",
        "required": ["server_time", "local_time", "rtt_ms"],
        "properties": {
            "server_time": {"type": "number"},
            "local_time": {"type": "number"},
            "rtt_ms": {"type": "number"},
            "limit_ms": {"type": "number"},
        },
    },
    "output_schema": {"type": "object", "required": ["skew_ms", "ok"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute `skew_ms = server_time - (local_time + rtt_ms / 2)` (DP-17..18)."""
    if "server_time" not in payload or "local_time" not in payload:
        raise ValueError("server_time and local_time are required")
    server_ms = float(payload["server_time"])
    local_ms = float(payload["local_time"])
    rtt_ms = max(0.0, float(payload.get("rtt_ms", 0.0)))
    limit_ms = float(payload.get("limit_ms", 1000.0))
    skew_ms = round(server_ms - (local_ms + rtt_ms / 2.0), 3)
    return {"skew_ms": skew_ms, "ok": abs(skew_ms) <= limit_ms}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
