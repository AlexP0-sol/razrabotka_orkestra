"""Built-in skill: verify Python imports and symbol attributes exist in installed environment."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "import_verifier",
    "name": "Anti-Hallucination Import & Symbol Verifier",
    "version": "1.0.0",
    "description": "Verify imported modules and from-import symbols exist in the runtime env.",
    "tags": ("grounding", "imports", "anti_hallucination", "python"),
    "input_schema": {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {"type": "string"},
            "inspect_attributes": {"type": "boolean"},
            "search_roots": {"type": "array"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["valid", "missing_modules", "missing_symbols"],
        "properties": {
            "valid": {"type": "boolean"},
            "missing_modules": {"type": "array"},
            "missing_symbols": {"type": "array"},
        },
    },
}


def _safe_find_spec(module_name: str) -> object | None:
    try:
        return importlib.util.find_spec(module_name)
    except (ModuleNotFoundError, ValueError):
        return None


def _exists_in_roots(module_name: str, roots: list[str]) -> bool:
    from pathlib import Path

    rel_parts = module_name.split(".")
    for raw_root in roots:
        base = Path(raw_root)
        mod_py = base.joinpath(*rel_parts).with_suffix(".py")
        pkg_init = base.joinpath(*rel_parts) / "__init__.py"
        if mod_py.is_file() or pkg_init.is_file():
            return True
    return False


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify imports in `payload['source']` against installed packages and `search_roots`."""
    source = str(payload["source"])
    check_attrs = bool(payload.get("inspect_attributes", True))
    roots = [str(r) for r in payload.get("search_roots", ())]
    tree = ast.parse(source)

    missing_modules: list[str] = []
    missing_symbols: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _exists_in_roots(alias.name, roots):
                    continue
                if _safe_find_spec(alias.name) is None:
                    missing_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or not node.module:
                continue
            if _exists_in_roots(node.module, roots):
                continue
            if _safe_find_spec(node.module) is None:
                missing_modules.append(node.module)
                continue
            if check_attrs:
                try:
                    mod = importlib.import_module(node.module)
                except Exception:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if not hasattr(mod, alias.name):
                        sub_spec = _safe_find_spec(f"{node.module}.{alias.name}")
                        if sub_spec is None:
                            missing_symbols.append(f"{node.module}.{alias.name}")

    dedup_mods = sorted(set(missing_modules))
    dedup_syms = sorted(set(missing_symbols))
    return {
        "valid": not dedup_mods and not dedup_syms,
        "missing_modules": dedup_mods,
        "missing_symbols": dedup_syms,
    }


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
