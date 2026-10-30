"""Cross-platform Subprocess & Docker sandboxes with tree-kill and path guards (§13.11, §13.21)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from orchestra.config.constants import (
    ERROR_TAIL_LINES,
    GRACE_KILL_S,
    OUTPUT_CAPTURE_LIMIT,
    PATH_LENGTH_WARN,
    SANDBOX_CPU_LIMIT,
    SANDBOX_MEMORY_LIMIT_MB,
    SANDBOX_TIMEOUT_S,
)
from orchestra.errors import DisallowedCommand, SandboxTimeoutError, SecurityViolation
from orchestra.skills.executor import build_sanitized_skill_env

ALLOWED_SANDBOX_BINARIES: frozenset[str] = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "ruff",
        "mypy",
        "git",
    }
)


class SandboxResult(BaseModel):
    """Structured result of running a whitelisted command inside a sandbox (SB-06)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    tail_error: str
    duration_ms: float
    path_length_warning: str | None = None


def resolve_safe_workspace_path(workspace_root: Path, rel_path: str) -> tuple[Path, str | None]:
    """Resolve `rel_path` strictly inside `workspace_root`, blocking traversal/symlinks (SEC-05)."""
    normalized = rel_path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/")[0]:
        raise SecurityViolation(f"Absolute or drive-prefixed path rejected: '{rel_path}'")

    root_resolved = workspace_root.resolve()
    candidate = (root_resolved / normalized).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise SecurityViolation(f"Path traversal outside workspace blocked: '{rel_path}'") from exc

    warn: str | None = None
    if len(str(candidate)) > PATH_LENGTH_WARN:
        warn = (
            f"Path length {len(str(candidate))} exceeds Windows MAX_PATH warning "
            f"threshold {PATH_LENGTH_WARN}: {candidate}"
        )
    return candidate, warn


def validate_whitelisted_command(argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Verify command executable belongs to `ALLOWED_SANDBOX_BINARIES` or `sys.executable`."""
    if not argv:
        raise DisallowedCommand("Empty command argv is not permitted")
    exe = argv[0]
    exe_path = Path(exe)
    stem = exe_path.stem.lower()
    sys_exe_resolved = str(Path(sys.executable).resolve())
    is_current_python = exe == sys.executable or (
        exe_path.is_absolute() and str(exe_path.resolve()) == sys_exe_resolved
    )
    if not is_current_python and stem not in ALLOWED_SANDBOX_BINARIES:
        raise DisallowedCommand(
            f"Executable '{exe}' (stem='{stem}') is not in sandbox whitelist (SEC-06)"
        )
    return tuple(argv)


async def _terminate_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace_s: float = GRACE_KILL_S,
) -> None:
    """Terminate an entire subprocess group/tree cleanly on both Windows and POSIX (WIN-02)."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        kill_proc = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill_proc.wait()
        with suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return

    with suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except TimeoutError:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        await proc.wait()


def _truncate_and_tail(raw_bytes: bytes) -> tuple[str, str]:
    clipped = raw_bytes[:OUTPUT_CAPTURE_LIMIT].decode("utf-8", errors="replace")
    lines = clipped.splitlines()
    tail = "\n".join(lines[-ERROR_TAIL_LINES:])
    return clipped, tail


class ExecutionSandbox:
    """Unified Subprocess / Docker execution sandbox for verifiers and tests (§13.11)."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        mode: Literal["subprocess", "docker"] = "subprocess",
        timeout_s: float = SANDBOX_TIMEOUT_S,
        memory_limit_mb: int = SANDBOX_MEMORY_LIMIT_MB,
        cpu_limit: float = SANDBOX_CPU_LIMIT,
        docker_image: str = "python:3.12-slim",
    ) -> None:
        """Initialize ExecutionSandbox rooted at `workspace_dir`."""
        self.workspace_dir = workspace_dir.resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._timeout_s = timeout_s
        self._memory_mb = memory_limit_mb
        self._cpu_limit = cpu_limit
        self._docker_image = docker_image

    async def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        timeout_s: float | None = None,
    ) -> SandboxResult:
        """Run whitelisted `argv` inside `workspace_dir` with timeout & tree-kill guarantees."""
        checked_argv = validate_whitelisted_command(argv)
        eff_timeout = timeout_s if timeout_s is not None else self._timeout_s

        if self._mode == "docker":
            exec_argv: tuple[str, ...] = (
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                f"{self._memory_mb}m",
                "--cpus",
                str(self._cpu_limit),
                "--read-only",
                "--tmpfs",
                "/tmp",
                "-v",
                f"{self.workspace_dir}:/workspace:rw",
                "-w",
                "/workspace",
                self._docker_image,
                *checked_argv,
            )
        else:
            exec_argv = checked_argv

        env = build_sanitized_skill_env(extra_pythonpath=self.workspace_dir)
        start_t = time.monotonic()

        popen_kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        else:
            popen_kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(
            *exec_argv,
            cwd=str(self.workspace_dir),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **popen_kwargs,  # type: ignore[arg-type]
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=eff_timeout)
        except TimeoutError as exc:
            await _terminate_process_tree(proc)
            raise SandboxTimeoutError(
                f"Command {checked_argv!r} timed out after {eff_timeout:.1f}s"
            ) from exc

        elapsed_ms = round((time.monotonic() - start_t) * 1000, 2)
        stdout_txt, stdout_tail = _truncate_and_tail(stdout_b)
        stderr_txt, stderr_tail = _truncate_and_tail(stderr_b)
        combined_tail = stderr_tail if stderr_tail.strip() else stdout_tail

        path_warn = (
            f"Workspace path length {len(str(self.workspace_dir))} > {PATH_LENGTH_WARN}"
            if len(str(self.workspace_dir)) > PATH_LENGTH_WARN
            else None
        )
        return SandboxResult(
            argv=checked_argv,
            exit_code=int(proc.returncode or 0),
            stdout=stdout_txt,
            stderr=stderr_txt,
            tail_error=combined_tail,
            duration_ms=elapsed_ms,
            path_length_warning=path_warn,
        )
