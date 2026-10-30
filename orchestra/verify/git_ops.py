"""Safe Git operations wrapper for generated project workspaces (§13.13, WIN-07)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from orchestra.errors import OrchestraError

if TYPE_CHECKING:
    from pathlib import Path

PROTECTED_MAIN_BRANCHES: frozenset[str] = frozenset({"main", "master"})


class GitOperationError(OrchestraError):
    """Raised when a workspace `git` command exits with a non-zero status."""


class GitOps:
    """Executes shell-free `git` commands inside a project workspace with LF normalization."""

    def __init__(
        self,
        repo_dir: Path,
        *,
        forbid_direct_main_commits: bool = True,
    ) -> None:
        """Initialize GitOps for `repo_dir`."""
        self.repo_dir = repo_dir.resolve()
        self._forbid_main = forbid_direct_main_commits

    async def _git(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self.repo_dir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        out = stdout_b.decode("utf-8", errors="replace").strip()
        err = stderr_b.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            raise GitOperationError(
                f"git {' '.join(args)} failed ({proc.returncode}): {err or out}"
            )
        return out

    async def init_workspace_repo(self, *, working_branch: str = "orchestra/work") -> None:
        """Initialize git repository with LF config and switch onto `working_branch`."""
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        if not (self.repo_dir / ".git").exists():
            await self._git("init", "-b", working_branch)
        await self._git("config", "core.autocrlf", "false")
        await self._git("config", "core.eol", "lf")
        await self._git("config", "user.name", "ORCHESTRA Swarm")
        await self._git("config", "user.email", "orchestra@local.invalid")
        cur = await self.current_branch()
        if cur != working_branch:
            await self.checkout_branch(working_branch, create=True)

    async def current_branch(self) -> str:
        """Return active branch name (including unborn initial branch)."""
        branch = await self._git("branch", "--show-current")
        if branch:
            return branch
        return await self._git("rev-parse", "--abbrev-ref", "HEAD")

    async def checkout_branch(self, branch_name: str, *, create: bool = False) -> None:
        """Switch to `branch_name` (creating it with `-B` when `create=True`)."""
        if create:
            await self._git("checkout", "-B", branch_name)
        else:
            await self._git("checkout", branch_name)

    async def status_porcelain(self) -> str:
        """Return `git status --porcelain` output."""
        return await self._git("status", "--porcelain")

    async def commit_all(self, message: str) -> str:
        """Stage all workspace changes and create an atomic commit, returning short SHA."""
        if self._forbid_main:
            branch = await self.current_branch()
            if branch in PROTECTED_MAIN_BRANCHES:
                raise GitOperationError(
                    f"Direct commits to protected branch '{branch}' are forbidden (FP-11)"
                )
        await self._git("add", "-A")
        status = await self.status_porcelain()
        if not status:
            return await self.head_sha()
        await self._git("commit", "-m", message)
        return await self.head_sha()

    async def head_sha(self) -> str:
        """Return short commit hash of `HEAD`."""
        return await self._git("rev-parse", "--short", "HEAD")
