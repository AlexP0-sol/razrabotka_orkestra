"""Built-in skill: detect unreachable statements and unused private functions in Python AST."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "dead_code_finder",
    "name": "Unreachable & Dead Private Code Finder",
    "version": "1.0.0",
    "description": "Detect unreachable statements after return/raise and unused private helpers.",
    "tags": ("quality", "ast", "dead_code", "python"),
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "file_path": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["unused_private", "unreachable_lines"],
        "properties": {
            "unused_private": {"type": "array"},
            "unreachable_lines": {"type": "array"},
        },
    },
}

_TERMINAL_STMTS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze Python AST for dead private functions and unreachable lines."""
    source = str(payload.get("source") or "")
    if not source and payload.get("file_path"):
        source = Path(str(payload["file_path"])).read_text(encoding="utf-8")

    tree = ast.parse(source)
    private_defs: set[str] = set()
    used_names: set[str] = set()
    unreachable_lines: list[int] = []

    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
            and not node.name.startswith("__")
        ):
            private_defs.add(node.name)

    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.Name) and isinstance(ast_node.ctx, ast.Load):
            used_names.add(ast_node.id)
        elif isinstance(ast_node, ast.Attribute):
            used_names.add(ast_node.attr)

        body = getattr(ast_node, "body", None)
        if isinstance(body, list):
            terminated = False
            for stmt in body:
                if terminated and isinstance(stmt, ast.stmt):
                    unreachable_lines.append(stmt.lineno)
                if isinstance(stmt, _TERMINAL_STMTS):
                    terminated = True

    unused = sorted(private_defs - used_names - {"_main"})
    return {"unused_private": unused, "unreachable_lines": sorted(set(unreachable_lines))}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
