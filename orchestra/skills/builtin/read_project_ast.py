"""Builtin skill §13.9.3: extract AST symbols, imports, and signatures without raw file noise."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "read_project_ast",
    "name": "Read Project AST",
    "version": "1.0.0",
    "description": "Extract top-level symbols, imports, and function signatures from Python files.",
    "tags": ("ast", "python", "outline"),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "source": {"type": "string"},
            "lang": {"type": "string"},
        },
    },
    "output_schema": {"type": "object", "required": ["symbols", "imports", "signatures"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse Python source or file path and return structured symbols, imports, and signatures."""
    src = (
        str(payload["source"])
        if "source" in payload
        else Path(str(payload["path"])).read_text(encoding="utf-8")
    )
    tree = ast.parse(src)
    symbols: list[str] = []
    imports: list[str] = []
    signatures: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
            args = [a.arg for a in node.args.args]
            signatures.append(f"def {node.name}({', '.join(args)})")

    return {"symbols": symbols, "imports": sorted(set(imports)), "signatures": signatures}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
