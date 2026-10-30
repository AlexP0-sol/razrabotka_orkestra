"""Built-in skill: identify public functions/classes in target code not referenced in tests."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "test_coverage_gap",
    "name": "Static Test Reference Gap Finder",
    "version": "1.0.0",
    "description": "Find public classes and functions in target module never referenced in tests.",
    "tags": ("testing", "coverage", "qa", "python"),
    "input_schema": {
        "type": "object",
        "required": ["target_source", "test_source"],
        "properties": {
            "target_source": {"type": "string"},
            "test_source": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["public_symbols", "unreferenced_symbols", "coverage_ratio"],
        "properties": {
            "public_symbols": {"type": "array"},
            "unreferenced_symbols": {"type": "array"},
            "coverage_ratio": {"type": "number"},
        },
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare top-level public symbols in `target_source` against AST names in `test_source`."""
    t_src = str(payload["target_source"])
    if Path(t_src).is_file():
        t_src = Path(t_src).read_text(encoding="utf-8")
    u_src = str(payload["test_source"])
    if Path(u_src).is_file():
        u_src = Path(u_src).read_text(encoding="utf-8")

    t_tree = ast.parse(t_src)
    public: list[str] = []
    for node in t_tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and not node.name.startswith("_"):
            public.append(node.name)

    u_tree = ast.parse(u_src)
    referenced: set[str] = set()
    for ast_node in ast.walk(u_tree):
        if isinstance(ast_node, ast.Name):
            referenced.add(ast_node.id)
        elif isinstance(ast_node, ast.Attribute):
            referenced.add(ast_node.attr)

    missing = [sym for sym in public if sym not in referenced]
    ratio = 1.0 if not public else round((len(public) - len(missing)) / len(public), 4)
    return {
        "public_symbols": public,
        "unreferenced_symbols": missing,
        "coverage_ratio": ratio,
    }


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
