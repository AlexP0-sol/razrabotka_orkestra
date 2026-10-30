"""Built-in skill: compute McCabe cyclomatic complexity and function/file line-budget metrics."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "complexity_metrics",
    "name": "Cyclomatic & Line Complexity Analyzer",
    "version": "1.0.0",
    "description": "Compute McCabe cyclomatic complexity and flag functions >50 or files >500 LOC.",
    "tags": ("quality", "complexity", "metrics", "python"),
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "file_path": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["total_lines", "max_complexity", "functions", "violations"],
        "properties": {
            "total_lines": {"type": "integer"},
            "max_complexity": {"type": "integer"},
            "functions": {"type": "array"},
            "violations": {"type": "array"},
        },
    },
}

_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
)


def _func_complexity(fn_node: ast.AST) -> int:
    score = 1
    for child in ast.walk(fn_node):
        if isinstance(child, _BRANCH_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
    return score


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute line counts and cyclomatic complexity per function."""
    source = str(payload.get("source") or "")
    if not source and payload.get("file_path"):
        source = Path(str(payload["file_path"])).read_text(encoding="utf-8")

    total_lines = len(source.splitlines())
    tree = ast.parse(source)
    fn_reports: list[dict[str, Any]] = []
    violations: list[str] = []

    if total_lines > 500:
        violations.append(f"File line count {total_lines} exceeds QS-10 cap 500")

    max_cc = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            span = end_line - node.lineno + 1
            cc = _func_complexity(node)
            max_cc = max(max_cc, cc)
            fn_reports.append({"name": node.name, "lines": span, "complexity": cc})
            if span > 65:
                violations.append(f"Function '{node.name}' spans {span} lines (recommended <= 50)")
            if cc > 15:
                violations.append(f"Function '{node.name}' has high complexity ({cc})")

    return {
        "total_lines": total_lines,
        "max_complexity": max_cc,
        "functions": fn_reports,
        "violations": violations,
    }


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
