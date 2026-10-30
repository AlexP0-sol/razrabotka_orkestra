"""Builtin skill §13.9.3: append entry to role discovery diary through Empirical Proof Gate."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "append_to_diary",
    "name": "Append To Discovery Diary",
    "version": "1.0.0",
    "description": "Validate and append a HYPOTHESIS or VERIFIED entry to DISCOVERY_DIARY.",
    "tags": ("diary", "talents", "memory"),
    "input_schema": {
        "type": "object",
        "required": ["talents_dir", "entry"],
        "properties": {
            "talents_dir": {"type": "string"},
            "entry": {"type": "object"},
        },
    },
    "output_schema": {"type": "object", "required": ["entry_id", "status"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate `entry` via `DiaryEntry` (enforcing `DiaryGateViolation` on VERIFIED) and store."""
    dm_mod = importlib.import_module("orchestra.talents.diary_manager")
    talents_dir = Path(str(payload["talents_dir"])).resolve()
    entry = dm_mod.DiaryEntry.model_validate(payload["entry"])
    mgr = dm_mod.DiaryManager(talents_dir)
    stored = mgr.append(entry)
    return {"entry_id": str(stored.id), "status": str(stored.status)}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
