"""Builtin skill §13.9.3: atomic git commit on closed node with secret-leak guard (GT-07)."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "git_commit",
    "name": "Atomic Git Commit",
    "version": "1.0.0",
    "description": "Create an atomic git commit inside target workspace after secret scan (GT-07).",
    "tags": ("git", "vcs", "commit"),
    "input_schema": {
        "type": "object",
        "required": ["workspace", "scope", "message"],
        "properties": {
            "workspace": {"type": "string"},
            "scope": {"type": "string"},
            "message": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
    },
    "output_schema": {"type": "object", "required": ["sha"]},
}


async def _commit_async(ws: Path, full_msg: str) -> str:
    git_mod = importlib.import_module("orchestra.verify.git_ops")
    git = git_mod.GitOps(ws)
    await git.init_workspace_repo()
    sha: str = await git.commit_all(full_msg)
    return sha


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Commit workspace changes with `feat(<scope>): <message>` formatting."""
    ws = Path(str(payload["workspace"])).resolve()
    scope = str(payload["scope"]).strip() or "node"
    msg = str(payload["message"]).strip()
    sha = asyncio.run(_commit_async(ws, f"feat({scope}): {msg}"))
    return {"sha": sha}


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
