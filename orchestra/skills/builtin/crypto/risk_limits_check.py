"""Crypto skill: fail-closed pre-trade risk limit evaluator (TR-05, DP-34, DP-35)."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "risk_limits_check",
    "name": "Pre-Trade Risk Limits Gate",
    "version": "1.0.0",
    "description": "Evaluate OrderIntent against notional, exposure, loss, and symbol limits.",
    "tags": ("crypto", "risk", "pretrade", "limits"),
    "input_schema": {
        "type": "object",
        "required": ["intent", "limits", "portfolio"],
        "properties": {
            "intent": {"type": "object"},
            "limits": {"type": "object"},
            "portfolio": {"type": "object"},
        },
    },
    "output_schema": {"type": "object", "required": ["allowed", "violations"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Check `intent` against `limits` and `portfolio` using exact `Decimal` comparisons."""
    if not all(k in payload for k in ("intent", "limits", "portfolio")):
        raise ValueError("intent, limits, and portfolio are required")
    intent = dict(payload["intent"])
    limits = dict(payload["limits"])
    portfolio = dict(payload["portfolio"])
    violations: list[str] = []

    symbol = str(intent.get("symbol", ""))
    allowed_syms = set(limits.get("allowed_symbols", []))
    forbidden_syms = set(limits.get("forbidden_symbols", []))
    if allowed_syms and symbol not in allowed_syms:
        violations.append(f"SYMBOL_NOT_ALLOWED:{symbol}")
    if symbol in forbidden_syms:
        violations.append(f"SYMBOL_FORBIDDEN:{symbol}")

    price = Decimal(str(intent.get("price", "0")))
    qty = Decimal(str(intent.get("qty", "0")))
    notional = price * qty

    if "max_notional_per_order" in limits and notional > Decimal(
        str(limits["max_notional_per_order"])
    ):
        violations.append("MAX_NOTIONAL_PER_ORDER_EXCEEDED")

    cur_exp = Decimal(str(portfolio.get("total_exposure", "0")))
    if "max_total_exposure" in limits and (cur_exp + notional) > Decimal(
        str(limits["max_total_exposure"])
    ):
        violations.append("MAX_TOTAL_EXPOSURE_EXCEEDED")

    daily_loss = Decimal(str(portfolio.get("daily_loss_quote", "0")))
    if "max_daily_loss_quote" in limits and daily_loss >= Decimal(
        str(limits["max_daily_loss_quote"])
    ):
        violations.append("MAX_DAILY_LOSS_EXCEEDED")

    open_orders = int(portfolio.get("open_orders", 0))
    if "max_open_orders" in limits and open_orders >= int(limits["max_open_orders"]):
        violations.append("MAX_OPEN_ORDERS_EXCEEDED")

    return {"allowed": len(violations) == 0, "violations": violations}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
