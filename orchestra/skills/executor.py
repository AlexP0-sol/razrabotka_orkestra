"""Isolated subprocess executor for ORCHESTRA skills with env scrubbing and schemas (§20.2)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from orchestra.config.constants import OUTPUT_CAPTURE_LIMIT
from orchestra.errors import SkillExecutionError
from orchestra.skills.manifest import SkillSpec

SCRUBBED_ENV_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSPHRASE",
    "CREDENTIAL",
)


def build_sanitized_skill_env(extra_pythonpath: Path | None = None) -> dict[str, str]:
    """Build minimal subprocess environment stripped of all secret variables (SEC-02)."""
    clean: dict[str, str] = {}
    keep_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL"}
    for key, val in os.environ.items():
        upper = key.upper()
        if any(sub in upper for sub in SCRUBBED_ENV_SUBSTRINGS):
            continue
        if upper in keep_keys or upper.startswith("PYTHON"):
            clean[key] = val
    clean["PYTHONUTF8"] = "1"
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    repo_root = str(Path(__file__).resolve().parents[2])
    if extra_pythonpath is not None:
        clean["PYTHONPATH"] = os.pathsep.join([str(extra_pythonpath), repo_root])
    else:
        clean["PYTHONPATH"] = repo_root
    return clean


def validate_against_minimal_schema(
    instance: Any,
    schema: dict[str, Any],
    *,
    label: str,
) -> None:
    """Validate `instance` against JSON Schema core keywords (`type`, `required`, `properties`)."""
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            raise SkillExecutionError(
                f"{label} must be a JSON object, got {type(instance).__name__}"
            )
        for req_key in schema.get("required", ()):
            if req_key not in instance:
                raise SkillExecutionError(f"{label} missing required property '{req_key}'")
        props = schema.get("properties", {})
        for prop_name, prop_schema in props.items():
            if prop_name in instance and isinstance(prop_schema, dict):
                validate_against_minimal_schema(
                    instance[prop_name],
                    prop_schema,
                    label=f"{label}.{prop_name}",
                )
    elif expected_type == "array":
        if not isinstance(instance, list):
            raise SkillExecutionError(f"{label} must be an array")
    elif expected_type == "string":
        if not isinstance(instance, str):
            raise SkillExecutionError(f"{label} must be a string")
    elif expected_type == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            raise SkillExecutionError(f"{label} must be an integer")
    elif expected_type == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            raise SkillExecutionError(f"{label} must be a number")
    elif expected_type == "boolean" and not isinstance(instance, bool):
        raise SkillExecutionError(f"{label} must be a boolean")

    if "enum" in schema and instance not in schema["enum"]:
        raise SkillExecutionError(f"{label} value {instance!r} not in enum {schema['enum']}")


class SkillExecutor:
    """Executes registered skills in isolated Python subprocesses via JSON stdin/stdout."""

    def __init__(self, *, working_dir: Path | None = None) -> None:
        """Initialize SkillExecutor."""
        self._cwd = working_dir

    async def execute(self, spec: SkillSpec, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate input, run `spec.entrypoint_path` in subprocess, and validate JSON output."""
        validate_against_minimal_schema(payload, spec.input_schema, label=f"{spec.id}.input")

        entry = Path(spec.entrypoint_path)
        exists = await asyncio.to_thread(entry.is_file)
        if not exists:
            raise SkillExecutionError(f"Skill entrypoint not found: {entry}")

        stdin_bytes = json.dumps(payload).encode("utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(entry),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._cwd) if self._cwd else None,
            env=build_sanitized_skill_env(),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_bytes),
                timeout=spec.timeout_s,
            )
        except TimeoutError as exc:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise SkillExecutionError(
                f"Skill '{spec.id}' timed out after {spec.timeout_s:.1f}s"
            ) from exc

        stdout_txt = stdout_b[:OUTPUT_CAPTURE_LIMIT].decode("utf-8", errors="replace").strip()
        stderr_txt = stderr_b[:OUTPUT_CAPTURE_LIMIT].decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            raise SkillExecutionError(
                f"Skill '{spec.id}' exited with code {proc.returncode}: {stderr_txt[:400]}"
            )
        try:
            output_obj = json.loads(stdout_txt)
        except json.JSONDecodeError as exc:
            raise SkillExecutionError(
                f"Skill '{spec.id}' returned non-JSON stdout: {stdout_txt[:200]}"
            ) from exc

        if not isinstance(output_obj, dict):
            raise SkillExecutionError(f"Skill '{spec.id}' output must be a JSON object")

        validate_against_minimal_schema(output_obj, spec.output_schema, label=f"{spec.id}.output")
        return output_obj
