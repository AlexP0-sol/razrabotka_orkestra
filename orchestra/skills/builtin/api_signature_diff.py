"""Built-in skill: compute structural diff of public classes and functions between two versions."""

from __future__ import annotations

import ast
import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "api_signature_diff",
    "name": "Public API Signature Diff",
    "version": "1.0.0",
    "description": "Compute added, removed, and modified public signatures between Python files.",
    "tags": ("api", "diff", "contract", "python"),
    "input_schema": {
        "type": "object",
        "required": ["before_source", "after_source"],
        "properties": {
            "before_source": {"type": "string"},
            "after_source": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["added", "removed", "modified"],
        "properties": {
            "added": {"type": "array"},
            "removed": {"type": "array"},
            "modified": {"type": "array"},
        },
    },
}


def _public_api_map(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    api: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            args = [a.arg for a in node.args.args]
            kw = [a.arg for a in node.args.kwonlyargs]
            api[node.name] = f"fn({','.join(args)};kw={','.join(kw)})"
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = sorted(
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            )
            api[node.name] = f"cls({','.join(methods)})"
    return api


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Return added, removed, and modified public symbols."""
    before = _public_api_map(str(payload["before_source"]))
    after = _public_api_map(str(payload["after_source"]))

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
