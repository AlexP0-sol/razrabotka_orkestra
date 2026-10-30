"""Crypto skill: exact Decimal normalization under quantum (`ROUND_DOWN` / `ROUND_HALF_UP`)."""

from __future__ import annotations

import json
import sys
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, localcontext
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "decimal_money",
    "name": "Decimal Quantum Normalizer",
    "version": "1.0.0",
    "description": "Quantize monetary/price/quantity Decimal strings under exchange quantum.",
    "tags": ("crypto", "decimal", "money", "quantum"),
    "input_schema": {
        "type": "object",
        "required": ["value", "quantum"],
        "properties": {
            "value": {"type": "string"},
            "quantum": {"type": "string"},
            "mode": {"type": "string"},
        },
    },
    "output_schema": {"type": "object", "required": ["normalized"]},
}

_MODES = {
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "ROUND_HALF_UP": ROUND_HALF_UP,
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Quantize `value` string to `quantum` without float precision drift (DP-13..16)."""
    raw_val = payload.get("value")
    raw_q = payload.get("quantum")
    if isinstance(raw_val, float) or isinstance(raw_q, float):
        raise ValueError("float input is forbidden in decimal_money (TR-01)")
    if raw_val is None or raw_q is None:
        raise ValueError("value and quantum are required")
    mode_name = str(payload.get("mode", "ROUND_DOWN")).upper()
    rounding = _MODES.get(mode_name, ROUND_DOWN)
    with localcontext() as ctx:
        ctx.prec = 28
        val = Decimal(str(raw_val))
        quantum = Decimal(str(raw_q))
        quantized = val.quantize(quantum, rounding=rounding)
    return {"normalized": format(quantized, "f")}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
