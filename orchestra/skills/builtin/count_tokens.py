"""Builtin skill §13.9.3: deterministic offline token count for context budget checks."""

from __future__ import annotations

import json
import sys
from typing import Any

from orchestra.providers.token_counter import count_tokens_sync

SKILL_METADATA: dict[str, Any] = {
    "id": "count_tokens",
    "name": "Count Tokens",
    "version": "1.0.0",
    "description": "Count tokens in text using ORCHESTRA's offline-safe TokenCounter.",
    "tags": ("tokens", "budget", "context"),
    "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    },
    "output_schema": {"type": "object", "required": ["tokens"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic token estimate for `text`."""
    return {"tokens": count_tokens_sync(str(payload["text"]))}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
