"""Built-in skill: extract AST classes, functions, signatures, and imports from Python code."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "ast_outline",
    "name": "Python AST Outline Extractor",
    "version": "1.0.0",
    "description": "Extract classes, functions, argument signatures, and imports from Python code.",
    "tags": ("ast", "python", "outline", "code"),
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "file_path": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["classes", "functions", "imports", "line_count"],
        "properties": {
            "classes": {"type": "array"},
            "functions": {"type": "array"},
            "imports": {"type": "array"},
            "line_count": {"type": "integer"},
        },
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse Python source or file and return structural outline."""
    source = str(payload.get("source") or "")
    if not source and payload.get("file_path"):
        source = Path(str(payload["file_path"])).read_text(encoding="utf-8")

    tree = ast.parse(source)
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({"name": node.name, "lineno": node.lineno, "methods": methods})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            functions.append(
                {
                    "name": node.name,
                    "lineno": node.lineno,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "args": args,
                }
            )
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    return {
        "classes": classes,
        "functions": functions,
        "imports": sorted(set(imports)),
        "line_count": len(source.splitlines()),
    }


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
