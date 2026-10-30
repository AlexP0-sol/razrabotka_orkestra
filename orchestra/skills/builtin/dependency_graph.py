"""Built-in skill: build intra-package Python import graph and detect import cycles."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "dependency_graph",
    "name": "Package Import Dependency Graph",
    "version": "1.0.0",
    "description": "Scan Python package to build module import graph and detect circular imports.",
    "tags": ("architecture", "imports", "dag", "python"),
    "input_schema": {
        "type": "object",
        "required": ["root_dir"],
        "properties": {
            "root_dir": {"type": "string"},
            "package_prefix": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["edges", "cycles", "module_count"],
        "properties": {
            "edges": {"type": "object"},
            "cycles": {"type": "array"},
            "module_count": {"type": "integer"},
        },
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Build adjacency map of Python imports under `root_dir` and report cycles."""
    root = Path(str(payload["root_dir"])).resolve()
    prefix = str(payload.get("package_prefix") or "")
    edges: dict[str, list[str]] = {}

    for py_file in sorted(root.rglob("*.py")):
        rel_parts = list(py_file.relative_to(root).with_suffix("").parts)
        if rel_parts[-1] == "__init__":
            rel_parts = rel_parts[:-1]
        mod_name = ".".join([p for p in (prefix, *rel_parts) if p]) or "__root__"
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        deps: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not prefix or alias.name.startswith(prefix):
                        deps.add(alias.name)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (not prefix or node.module.startswith(prefix))
            ):
                deps.add(node.module)
        deps.discard(mod_name)
        edges[mod_name] = sorted(deps)

    cycles = _find_cycles(edges)
    return {"edges": edges, "cycles": cycles, "module_count": len(edges)}


def _find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    color: dict[str, int] = dict.fromkeys(edges, 0)
    stack: list[str] = []
    cycles: list[list[str]] = []

    def _dfs(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in edges.get(node, ()):
            if nxt not in color:
                continue
            if color[nxt] == 1:
                idx = stack.index(nxt)
                cycles.append([*stack[idx:], nxt])
            elif color[nxt] == 0:
                _dfs(nxt)
        stack.pop()
        color[node] = 2

    for mod in sorted(edges):
        if color[mod] == 0:
            _dfs(mod)
    return cycles


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
