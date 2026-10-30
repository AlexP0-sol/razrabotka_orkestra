"""Crypto skill: deterministic JSONL WebSocket stream replay with dedup (DP-60, DP-26)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "ws_replay",
    "name": "Recorded WebSocket Stream Replayer",
    "version": "1.0.0",
    "description": "Replay recorded JSONL market events ordered by exchange_ts with deduplication.",
    "tags": ("crypto", "websocket", "replay", "fixtures"),
    "input_schema": {
        "type": "object",
        "properties": {
            "fixture_path": {"type": "string"},
            "lines": {"type": "array"},
            "speed": {"type": "number"},
        },
    },
    "output_schema": {"type": "object", "required": ["events", "deduped_count"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay recorded JSONL WS events deterministically sorted by `(exchange_ts, seq)`."""
    raw_lines: list[str] = []
    if payload.get("fixture_path"):
        raw_lines = Path(str(payload["fixture_path"])).read_text(encoding="utf-8").splitlines()
    elif isinstance(payload.get("lines"), list):
        raw_lines = [json.dumps(x) if isinstance(x, dict) else str(x) for x in payload["lines"]]

    seen_ids: set[str] = set()
    deduped = 0
    parsed: list[dict[str, Any]] = []
    for idx, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            continue
        ev = json.loads(stripped)
        ev_id = str(
            ev.get("event_id") or f"{ev.get('exchange_ts', idx)}:{ev.get('lastUpdateId', idx)}"
        )
        if ev_id in seen_ids:
            deduped += 1
            continue
        seen_ids.add(ev_id)
        parsed.append(ev)

    parsed.sort(key=lambda e: (int(e.get("exchange_ts", 0)), int(e.get("seq", 0))))
    return {"events": parsed, "deduped_count": deduped}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
