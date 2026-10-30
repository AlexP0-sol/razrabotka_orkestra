"""Builtin skill §13.9.3: build validated escalation ticket for Architect / Human review."""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "escalate_to_architect",
    "name": "Escalate To Architect",
    "version": "1.0.0",
    "description": "Create a structured escalation ticket with numbered options for the Architect.",
    "tags": ("escalation", "comms", "architect"),
    "input_schema": {
        "type": "object",
        "required": ["question", "options", "node_id"],
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "node_id": {"type": "string"},
        },
    },
    "output_schema": {"type": "object", "required": ["ticket_id", "node_id", "options_count"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate escalation fields and issue deterministic-format ticket identifier."""
    question = str(payload["question"]).strip()
    options = [str(o).strip() for o in payload.get("options", []) if str(o).strip()]
    node_id = str(payload["node_id"]).strip()
    if not question or len(options) < 2:
        raise ValueError("Escalation requires non-empty question and at least 2 options")
    ticket_id = f"esc_{node_id}_{uuid.uuid4().hex[:8]}"
    return {"ticket_id": ticket_id, "node_id": node_id, "options_count": len(options)}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
