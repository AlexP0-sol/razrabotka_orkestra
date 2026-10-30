"""Built-in skill: verify backward compatibility between old and new Python Protocol contracts."""

from __future__ import annotations

import ast
import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "contract_compat_check",
    "name": "Contract Compatibility Checker",
    "version": "1.0.0",
    "description": "Check whether a modified Python contract preserves existing public signatures.",
    "tags": ("contract", "compatibility", "architecture"),
    "input_schema": {
        "type": "object",
        "required": ["old_code", "new_code"],
        "properties": {
            "old_code": {"type": "string"},
            "new_code": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["compatible", "breaking_changes"],
        "properties": {
            "compatible": {"type": "boolean"},
            "breaking_changes": {"type": "array"},
        },
    },
}


def _extract_callable_sigs(code: str) -> dict[str, tuple[list[str], int]]:
    tree = ast.parse(code)
    sigs: dict[str, tuple[list[str], int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and node.name != "__call__":
                continue
            args = [a.arg for a in node.args.args if a.arg != "self"]
            required_count = len(args) - len(node.args.defaults)
            sigs[node.name] = (args, max(0, required_count))
    return sigs


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare old and new Python contract definitions for breaking signature changes."""
    old_sigs = _extract_callable_sigs(str(payload["old_code"]))
    new_sigs = _extract_callable_sigs(str(payload["new_code"]))
    breaking: list[str] = []

    for fn_name, (old_args, old_req) in sorted(old_sigs.items()):
        if fn_name not in new_sigs:
            breaking.append(f"Removed public callable '{fn_name}'")
            continue
        new_args, new_req = new_sigs[fn_name]
        if new_req > old_req:
            breaking.append(
                f"Callable '{fn_name}' increased required positional args ({old_req} -> {new_req})"
            )
        if new_args[: len(old_args)] != old_args:
            breaking.append(f"Callable '{fn_name}' changed parameter order/names: {new_args}")

    return {"compatible": len(breaking) == 0, "breaking_changes": breaking}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
