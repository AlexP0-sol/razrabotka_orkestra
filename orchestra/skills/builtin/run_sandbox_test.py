"""Builtin skill §13.9.3: run sandboxed test command (`shell=False`) and return failure tail."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ALLOWED_TEST_RUNNERS: frozenset[str] = frozenset({"pytest", "python", "python3", "ruff", "mypy"})

SKILL_METADATA: dict[str, Any] = {
    "id": "run_sandbox_test",
    "name": "Run Sandbox Test",
    "version": "1.0.0",
    "description": "Execute whitelisted test command without shell and return failure tail.",
    "tags": ("test", "sandbox", "verify"),
    "input_schema": {
        "type": "object",
        "required": ["cmd"],
        "properties": {
            "cmd": {"type": "array", "items": {"type": "string"}},
            "workdir": {"type": "string"},
            "timeout_s": {"type": "number"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["exit_code", "stdout_tail", "stderr_tail", "failures"],
    },
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Run an argv command (`shell=False`) with whitelisted executable and extract failures."""
    raw_cmd = payload["cmd"]
    argv = [str(part) for part in raw_cmd] if isinstance(raw_cmd, list) else [str(raw_cmd)]
    if not argv:
        raise ValueError("cmd must not be empty")
    exe_name = Path(argv[0]).name.lower().removesuffix(".exe")
    if exe_name not in ALLOWED_TEST_RUNNERS:
        raise ValueError(f"Executable '{exe_name}' is not in ALLOWED_TEST_RUNNERS")
    if exe_name in {"python", "python3"}:
        argv[0] = sys.executable

    workdir = Path(str(payload.get("workdir", "."))).resolve()
    timeout_s = float(payload.get("timeout_s", 30.0))
    proc = subprocess.run(
        argv,
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
    out_lines = proc.stdout.splitlines()[-50:]
    err_lines = proc.stderr.splitlines()[-50:]
    failures = [ln for ln in (*out_lines, *err_lines) if "FAILED" in ln or "Error" in ln]
    return {
        "exit_code": proc.returncode,
        "stdout_tail": "\n".join(out_lines),
        "stderr_tail": "\n".join(err_lines),
        "failures": failures,
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
