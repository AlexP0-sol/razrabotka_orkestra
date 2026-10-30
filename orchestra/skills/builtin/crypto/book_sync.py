"""Crypto skill: L2 orderbook resynchronization by `lastUpdateId` continuity (DP-25, TR-10)."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "book_sync",
    "name": "L2 OrderBook Snapshot + Delta Synchronizer",
    "version": "1.0.0",
    "description": "Synchronize L2 orderbook snapshot with buffered WS deltas via lastUpdateId.",
    "tags": ("crypto", "orderbook", "sync", "websocket"),
    "input_schema": {
        "type": "object",
        "required": ["snapshot", "deltas"],
        "properties": {"snapshot": {"type": "object"}, "deltas": {"type": "array"}},
    },
    "output_schema": {"type": "object", "required": ["book", "ok", "dropped_count"]},
}


def _apply_side(levels: dict[str, str], updates: list[list[str]]) -> None:
    for price_str, qty_str in updates:
        if Decimal(str(qty_str)) == Decimal("0"):
            levels.pop(str(price_str), None)
        else:
            levels[str(price_str)] = str(qty_str)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply buffered L2 deltas onto REST snapshot verifying strict sequence continuity."""
    if "snapshot" not in payload or "deltas" not in payload:
        raise ValueError("snapshot and deltas are required")
    snap = dict(payload["snapshot"])
    deltas = list(payload["deltas"])
    snap_id = int(snap.get("lastUpdateId", 0))
    bids = {str(p): str(q) for p, q in snap.get("bids", []) if Decimal(str(q)) > 0}
    asks = {str(p): str(q) for p, q in snap.get("asks", []) if Decimal(str(q)) > 0}

    dropped = 0
    expected_prev: int | None = None
    last_id = snap_id

    for delta in deltas:
        u_first = int(delta.get("U", delta.get("prevLastUpdateId", 0)))
        u_last = int(delta.get("u", delta.get("lastUpdateId", 0)))
        if u_last <= snap_id:
            dropped += 1
            continue
        if expected_prev is None:
            if u_first > snap_id + 1:
                return {"book": {"bids": [], "asks": []}, "ok": False, "dropped_count": dropped}
        elif u_first != expected_prev + 1:
            return {"book": {"bids": [], "asks": []}, "ok": False, "dropped_count": dropped}

        _apply_side(bids, list(delta.get("bids", [])))
        _apply_side(asks, list(delta.get("asks", [])))
        expected_prev = u_last
        last_id = u_last

    sorted_bids = sorted(bids.items(), key=lambda kv: Decimal(kv[0]), reverse=True)
    sorted_asks = sorted(asks.items(), key=lambda kv: Decimal(kv[0]))
    return {
        "book": {
            "lastUpdateId": last_id,
            "bids": [[p, q] for p, q in sorted_bids],
            "asks": [[p, q] for p, q in sorted_asks],
        },
        "ok": True,
        "dropped_count": dropped,
    }


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
