"""Crypto skill: historical dataset quality gate checking gaps, dupes, and anomalies (DP-51)."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "data_quality_check",
    "name": "Market Data Quality Gate",
    "version": "1.0.0",
    "description": "Detect sequence/time gaps, duplicate events, non-positive prices, and spikes.",
    "tags": ("crypto", "data", "quality", "backtest"),
    "input_schema": {
        "type": "object",
        "properties": {
            "dataset_ref": {"type": "string"},
            "records": {"type": "array"},
            "expected_step_ms": {"type": "integer"},
        },
    },
    "output_schema": {"type": "object", "required": ["gaps", "dupes", "anomalies", "ok"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Inspect market bars/trades for gaps, duplicates, and price/volume anomalies (DP-51)."""
    records: list[dict[str, Any]] = []
    if payload.get("dataset_ref"):
        records = json.loads(Path(str(payload["dataset_ref"])).read_text(encoding="utf-8"))
    elif isinstance(payload.get("records"), list):
        records = list(payload["records"])
    else:
        raise ValueError("Provide either dataset_ref or records list")

    step_ms = int(payload.get("expected_step_ms", 60_000))
    gaps: list[dict[str, Any]] = []
    dupes: list[int] = []
    anomalies: list[dict[str, Any]] = []
    seen_ts: set[int] = set()
    prev_ts: int | None = None
    prev_price: Decimal | None = None

    for idx, rec in enumerate(records):
        ts = int(rec.get("exchange_ts", idx))
        price = Decimal(str(rec.get("close", rec.get("price", "0"))))
        vol = Decimal(str(rec.get("volume", rec.get("qty", "1"))))

        if ts in seen_ts:
            dupes.append(ts)
        seen_ts.add(ts)

        if prev_ts is not None and (ts - prev_ts) > step_ms:
            gaps.append({"from_ts": prev_ts, "to_ts": ts, "delta_ms": ts - prev_ts})
        prev_ts = ts

        if price <= 0 or vol < 0:
            anomalies.append({"ts": ts, "reason": "NON_POSITIVE_PRICE_OR_NEGATIVE_VOLUME"})
        elif prev_price is not None and prev_price > 0:
            jump = abs(price - prev_price) / prev_price
            if jump > Decimal("0.50"):
                anomalies.append({"ts": ts, "reason": "EXTREME_PRICE_SPIKE_GT_50PCT"})
        prev_price = price

    ok = len(gaps) == 0 and len(dupes) == 0 and len(anomalies) == 0
    return {"gaps": gaps, "dupes": dupes, "anomalies": anomalies, "ok": ok}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
