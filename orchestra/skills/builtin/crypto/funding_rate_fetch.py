"""Crypto skill: offline/fixture perpetual funding rate series loader (§38.12)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "funding_rate_fetch",
    "name": "Perpetual Funding Rate History Loader",
    "version": "1.0.0",
    "description": "Load normalized perpetual funding rate events within [from_ts, to_ts].",
    "tags": ("crypto", "funding", "perp", "data"),
    "input_schema": {
        "type": "object",
        "required": ["exchange", "symbol", "from", "to"],
        "properties": {
            "exchange": {"type": "string"},
            "symbol": {"type": "string"},
            "from": {"type": "integer"},
            "to": {"type": "integer"},
            "fixture_path": {"type": "string"},
        },
    },
    "output_schema": {"type": "object", "required": ["rates"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Return funding rate events strictly bounded by `[from, to]` in UTC ms."""
    for req in ("exchange", "symbol", "from", "to"):
        if req not in payload:
            raise ValueError(f"Missing required field: {req}")
    exch = str(payload["exchange"]).lower()
    sym = str(payload["symbol"]).upper()
    start_ts = int(payload["from"])
    end_ts = int(payload["to"])
    if end_ts < start_ts:
        raise ValueError("'to' timestamp must be >= 'from'")

    if payload.get("fixture_path"):
        raw = json.loads(Path(str(payload["fixture_path"])).read_text(encoding="utf-8"))
        filtered = [
            r for r in raw if int(r["funding_ts"]) >= start_ts and int(r["funding_ts"]) <= end_ts
        ]
        return {"rates": filtered}

    interval_ms = 8 * 3600 * 1000
    rates: list[dict[str, Any]] = []
    cur = ((start_ts + interval_ms - 1) // interval_ms) * interval_ms
    while cur <= end_ts and len(rates) < 500:
        rates.append(
            {
                "exchange": exch,
                "symbol": sym,
                "funding_ts": cur,
                "funding_rate": "0.00010000",
            }
        )
        cur += interval_ms
    return {"rates": rates}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
