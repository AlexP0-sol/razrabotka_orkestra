"""Builtin skill §13.9.3: apply atomic SEARCH_REPLACE or CREATE_FILE patch via `PatchEngine`."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "apply_patch",
    "name": "Apply Structured Patch",
    "version": "1.0.0",
    "description": "Apply atomic SEARCH_REPLACE or CREATE_FILE patch inside a project workspace.",
    "tags": ("patch", "edit", "workspace"),
    "input_schema": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "workspace": {"type": "string"},
            "path": {"type": "string"},
            "search": {"type": "string"},
            "replace": {"type": "string"},
            "mode": {"type": "string"},
        },
    },
    "output_schema": {"type": "object", "required": ["applied", "diff", "lines_changed"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply single-file patch using `PatchEngine` and return diff summary."""
    pe_mod = importlib.import_module("orchestra.verify.patch_engine")
    ws = Path(str(payload.get("workspace", "."))).resolve()
    rel_path = str(payload["path"])
    mode = str(payload.get("mode", "SEARCH_REPLACE")).upper()
    engine = pe_mod.PatchEngine(ws)

    if mode == "CREATE_FILE":
        content = str(payload.get("replace", ""))
        res = engine.apply_transaction(
            [pe_mod.CreateFileOp(path=rel_path, content=content)],
            allowed_owned_files={rel_path},
        )
        lines_changed = len(content.splitlines())
    else:
        search_txt = str(payload["search"])
        replace_txt = str(payload["replace"])
        res = engine.apply_transaction(
            [
                pe_mod.SearchReplaceOp(
                    path=rel_path,
                    blocks=(pe_mod.SearchReplaceBlock(search=search_txt, replace=replace_txt),),
                )
            ],
            allowed_owned_files={rel_path},
        )
        lines_changed = abs(len(replace_txt.splitlines()) - len(search_txt.splitlines())) + 1

    return {
        "applied": bool(res),
        "diff": f"{mode}:{rel_path}",
        "lines_changed": lines_changed,
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
