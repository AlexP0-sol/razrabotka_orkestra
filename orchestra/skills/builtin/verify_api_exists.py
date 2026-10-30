"""Builtin skill §13.9.3: runtime grounding check for package symbol existence & signature (§24)."""

from __future__ import annotations

import json
import sys
from typing import Any

from orchestra.errors import HallucinatedApiError
from orchestra.research.grounding import GroundingInspector

SKILL_METADATA: dict[str, Any] = {
    "id": "verify_api_exists",
    "name": "Verify API Exists",
    "version": "1.0.0",
    "description": "Verify at runtime whether a Python module/symbol exists and inspect signature.",
    "tags": ("grounding", "research", "anti-hallucination"),
    "input_schema": {
        "type": "object",
        "required": ["package", "symbol"],
        "properties": {
            "package": {"type": "string"},
            "symbol": {"type": "string"},
            "version": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["exists", "actual_signature", "doc_url"],
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Inspect runtime symbol via `GroundingInspector` without guessing."""
    pkg = str(payload["package"]).strip()
    sym = str(payload["symbol"]).strip()
    inspector = GroundingInspector()
    try:
        fact = inspector.inspect_symbol(f"{pkg}.{sym}")
        return {
            "exists": True,
            "actual_signature": fact.signature_str or sym,
            "doc_url": f"python://{pkg}.{sym}",
        }
    except HallucinatedApiError:
        return {"exists": False, "actual_signature": "", "doc_url": ""}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
