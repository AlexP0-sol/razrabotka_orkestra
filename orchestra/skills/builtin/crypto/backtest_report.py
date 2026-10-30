"""Crypto skill: deterministic backtest metrics & slippage sensitivity report (DP-45, DP-75)."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from decimal import Decimal
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "backtest_report",
    "name": "Deterministic Backtest Metrics & Slippage Sensitivity Builder",
    "version": "1.0.0",
    "description": "Compute Sharpe, Sortino, MaxDD, ProfitFactor, and slippage sensitivity table.",
    "tags": ("crypto", "backtest", "metrics", "report"),
    "input_schema": {
        "type": "object",
        "required": ["equity_curve", "fills"],
        "properties": {
            "equity_curve": {"type": "array"},
            "fills": {"type": "array"},
            "config": {"type": "object"},
        },
    },
    "output_schema": {"type": "object", "required": ["metrics", "tables", "report_sha256"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute required DP-45 backtest report metrics deterministically."""
    curve = [Decimal(str(x)) for x in list(payload.get("equity_curve") or [])]
    if len(curve) < 2:
        raise ValueError("equity_curve must contain at least 2 points")
    fills = list(payload.get("fills") or [])

    start_eq, end_eq = curve[0], curve[-1]
    total_return = (end_eq - start_eq) / start_eq if start_eq > 0 else Decimal("0")

    peak = curve[0]
    max_dd = Decimal("0")
    returns: list[float] = []
    for idx, val in enumerate(curve):
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else Decimal("0")
        if dd > max_dd:
            max_dd = dd
        if idx > 0 and curve[idx - 1] > 0:
            returns.append(float((val - curve[idx - 1]) / curve[idx - 1]))

    mean_r = sum(returns) / len(returns) if returns else 0.0
    var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns) if returns else 0.0
    std_r = math.sqrt(var_r)
    down_var = sum(min(0.0, r) ** 2 for r in returns) / len(returns) if returns else 0.0
    down_std = math.sqrt(down_var)
    sharpe = round((mean_r / std_r) * math.sqrt(252.0), 4) if std_r > 1e-12 else 0.0
    sortino = round((mean_r / down_std) * math.sqrt(252.0), 4) if down_std > 1e-12 else 0.0

    wins = [Decimal(str(f.get("pnl", "0"))) for f in fills if Decimal(str(f.get("pnl", "0"))) > 0]
    losses = [
        abs(Decimal(str(f.get("pnl", "0")))) for f in fills if Decimal(str(f.get("pnl", "0"))) < 0
    ]
    win_rate = round(len(wins) / len(fills), 4) if fills else 0.0
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))
    profit_factor = float(round(gross_profit / gross_loss, 4)) if gross_loss > 0 else 0.0
    total_notional = sum((Decimal(str(f.get("notional", "0"))) for f in fills), Decimal("0"))

    slippage_table = {}
    for bps in (0, 2, 5, 10):
        slip_cost = total_notional * (Decimal(bps) / Decimal("10000"))
        adj_end = end_eq - slip_cost
        slippage_table[f"{bps}_bps"] = format(adj_end, "f")

    metrics = {
        "total_return_pct": round(float(total_return * Decimal("100")), 4),
        "max_drawdown_pct": round(float(max_dd * Decimal("100")), 4),
        "sharpe": sharpe,
        "sortino": sortino,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "trades_count": len(fills),
        "disclaimer": "HISTORICAL SIMULATION ONLY — NOT INVESTMENT ADVICE (DP-73, DP-75)",
    }
    tables = {"slippage_sensitivity_equity": slippage_table}
    digest = hashlib.sha256(
        json.dumps({"metrics": metrics, "tables": tables}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"metrics": metrics, "tables": tables, "report_sha256": digest}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
