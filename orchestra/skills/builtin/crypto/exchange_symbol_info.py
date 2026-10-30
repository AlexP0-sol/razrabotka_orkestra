"""Crypto skill: instrument quantum (`tick_size`, `step_size`, `min_notional`) lookup (§38.12)."""

from __future__ import annotations

import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "exchange_symbol_info",
    "name": "Exchange Symbol Quantum & Permissions Info",
    "version": "1.0.0",
    "description": "Return tick_size, step_size, min_qty, min_notional, precision, and limits.",
    "tags": ("crypto", "exchange", "symbol", "quantum"),
    "input_schema": {
        "type": "object",
        "required": ["exchange", "symbol"],
        "properties": {"exchange": {"type": "string"}, "symbol": {"type": "string"}},
    },
    "output_schema": {
        "type": "object",
        "required": [
            "tick_size",
            "step_size",
            "min_qty",
            "min_notional",
            "precision",
            "permissions",
            "limits",
        ],
    },
}

_REFERENCE_SPECS: dict[tuple[str, str], dict[str, Any]] = {
    ("binance", "BTC/USDT"): {
        "tick_size": "0.01",
        "step_size": "0.00001",
        "min_qty": "0.00001",
        "min_notional": "5.00",
        "precision": {"price": 2, "qty": 5},
        "permissions": ["SPOT", "TRADE"],
        "limits": {"client_order_id_max_len": 36, "max_open_orders": 200},
    },
    ("bybit", "BTC/USDT:USDT"): {
        "tick_size": "0.10",
        "step_size": "0.001",
        "min_qty": "0.001",
        "min_notional": "5.00",
        "precision": {"price": 1, "qty": 3},
        "permissions": ["PERP", "TRADE"],
        "limits": {"client_order_id_max_len": 36, "max_open_orders": 500},
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic instrument rules for `(exchange, symbol)`."""
    exch = str(payload.get("exchange", "")).strip().lower()
    sym = str(payload.get("symbol", "")).strip().upper()
    if not exch or not sym:
        raise ValueError("exchange and symbol are required")
    spec = _REFERENCE_SPECS.get(
        (exch, sym),
        {
            "tick_size": "0.01",
            "step_size": "0.001",
            "min_qty": "0.001",
            "min_notional": "10.00",
            "precision": {"price": 2, "qty": 3},
            "permissions": ["SPOT", "TRADE"],
            "limits": {"client_order_id_max_len": 36, "max_open_orders": 200},
        },
    )
    return {"exchange": exch, "symbol": sym, **spec}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
