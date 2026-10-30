"""Builtin skill §13.9.3: list compact POSIX directory tree up to bounded depth."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SKIP_NAMES: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules"}
)

SKILL_METADATA: dict[str, Any] = {
    "id": "list_dir_tree",
    "name": "List Directory Tree",
    "version": "1.0.0",
    "description": "List relative POSIX paths inside a workspace up to `depth` levels.",
    "tags": ("filesystem", "tree", "workspace"),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "depth": {"type": "integer"},
        },
    },
    "output_schema": {"type": "object", "required": ["tree"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Walk `path` deterministically up to `depth` and return sorted POSIX paths."""
    root = Path(str(payload.get("path", "."))).resolve()
    max_depth = max(1, int(payload.get("depth", 2)))
    entries: list[str] = []

    def _walk(curr: Path, level: int) -> None:
        if level > max_depth:
            return
        for child in sorted(curr.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if child.name in SKIP_NAMES:
                continue
            rel = child.relative_to(root).as_posix()
            entries.append(f"{rel}/" if child.is_dir() else rel)
            if child.is_dir() and level < max_depth:
                _walk(child, level + 1)

    if root.is_dir():
        _walk(root, 1)
    return {"tree": entries}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
