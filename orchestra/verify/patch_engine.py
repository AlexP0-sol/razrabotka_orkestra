"""Transactional structured PatchEngine (CREATE_FILE, SEARCH_REPLACE, LINE_RANGE) (§24.2)."""

from __future__ import annotations

import ast
import hashlib
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import MAX_CHUNK_LINES
from orchestra.errors import (
    AmbiguousPatch,
    CaseCollisionError,
    FileOwnershipViolation,
    PatchError,
    PatchSyntaxError,
    StaleContextHashError,
)
from orchestra.state.event_log import atomic_write_text
from orchestra.state.mental_map import normalize_rel_posix_path
from orchestra.verify.sandbox import resolve_safe_workspace_path

if TYPE_CHECKING:
    from pathlib import Path


def compute_lines_sha256(lines: list[str]) -> str:
    """Compute canonical SHA-256 of LF-joined lines (without trailing newline sensitivity)."""
    joined = "\n".join(line.rstrip("\r") for line in lines)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class CreateFileOp(BaseModel):
    """Create or overwrite an owned file within chunk line budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["CREATE_FILE"] = "CREATE_FILE"
    path: str
    content: str


class SearchReplaceBlock(BaseModel):
    """Exact single-match search-and-replace block inside an existing file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search: str
    replace: str


class SearchReplaceOp(BaseModel):
    """Apply ordered single-match SEARCH_REPLACE blocks to an existing file (PT-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["SEARCH_REPLACE"] = "SEARCH_REPLACE"
    path: str
    blocks: tuple[SearchReplaceBlock, ...]


class LineRangeOp(BaseModel):
    """Replace 1-indexed inclusive `[start_line, end_line]` verified by SHA-256 (PT-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["LINE_RANGE"] = "LINE_RANGE"
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    expected_sha256: str
    replacement: str


PatchOperation = Annotated[
    CreateFileOp | SearchReplaceOp | LineRangeOp,
    Field(discriminator="op"),
]


class AppliedFileRecord(BaseModel):
    """Audit record of a file written by PatchEngine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rel_path: str
    sha256: str
    line_count: int


class PatchEngine:
    """Applies structured code edits with ownership, case-collision, AST, and rollback guards."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        max_chunk_lines: int = MAX_CHUNK_LINES,
    ) -> None:
        """Initialize PatchEngine rooted at `workspace_dir`."""
        self.workspace_dir = workspace_dir.resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._max_chunk_lines = max_chunk_lines

    def apply_transaction(
        self,
        operations: list[PatchOperation] | tuple[PatchOperation, ...],
        *,
        allowed_owned_files: set[str] | None = None,
    ) -> list[AppliedFileRecord]:
        """Validate all operations in memory, then write atomically with rollback on error."""
        if not operations:
            return []

        norm_allowed = (
            {normalize_rel_posix_path(p) for p in allowed_owned_files}
            if allowed_owned_files is not None
            else None
        )

        # Stage current file contents for multi-op edits and rollback
        staged: dict[str, tuple[Path, str | None, str]] = {}
        existing_case_map = self._scan_existing_case_map()

        for op in operations:
            rel_posix = normalize_rel_posix_path(op.path)
            if norm_allowed is not None and rel_posix not in norm_allowed:
                raise FileOwnershipViolation(
                    f"Patch target '{rel_posix}' is outside owned_files {sorted(norm_allowed)}"
                )
            abs_path, _ = resolve_safe_workspace_path(self.workspace_dir, rel_posix)

            folded = rel_posix.casefold()
            existing_rel = existing_case_map.get(folded)
            if existing_rel is not None and existing_rel != rel_posix:
                raise CaseCollisionError(
                    f"Case-insensitive collision in workspace: '{existing_rel}' vs '{rel_posix}'"
                )
            existing_case_map[folded] = rel_posix

            if rel_posix in staged:
                _, orig_content, current_content = staged[rel_posix]
            else:
                orig_content = abs_path.read_text(encoding="utf-8") if abs_path.is_file() else None
                current_content = orig_content or ""

            new_content = self._compute_patched_content(
                op, orig_exists=(orig_content is not None), current=current_content
            )
            if rel_posix.endswith(".py"):
                self._validate_python_syntax(new_content, rel_path=rel_posix)
            staged[rel_posix] = (abs_path, orig_content, new_content)

        written: list[tuple[Path, str | None]] = []
        records: list[AppliedFileRecord] = []
        try:
            for rel_posix, (abs_path, orig_content, final_content) in staged.items():
                lf_content = final_content.replace("\r\n", "\n").replace("\r", "\n")
                atomic_write_text(abs_path, lf_content)
                written.append((abs_path, orig_content))
                digest = hashlib.sha256(lf_content.encode("utf-8")).hexdigest()
                records.append(
                    AppliedFileRecord(
                        rel_path=rel_posix,
                        sha256=digest,
                        line_count=len(lf_content.splitlines()),
                    )
                )
        except Exception:
            for abs_path, orig_content in reversed(written):
                if orig_content is None:
                    abs_path.unlink(missing_ok=True)
                else:
                    atomic_write_text(abs_path, orig_content)
            raise

        return records

    def _compute_patched_content(
        self,
        op: PatchOperation,
        *,
        orig_exists: bool,
        current: str,
    ) -> str:
        if isinstance(op, CreateFileOp):
            lines = op.content.splitlines()
            if len(lines) > self._max_chunk_lines:
                raise PatchError(
                    f"CREATE_FILE '{op.path}' has {len(lines)} lines > max_chunk_lines "
                    f"{self._max_chunk_lines}; split into incremental SEARCH_REPLACE chunks (C-05)"
                )
            return op.content

        if not orig_exists and not current:
            raise PatchError(f"Target file '{op.path}' does not exist for {op.op}")

        if isinstance(op, SearchReplaceOp):
            text = current.replace("\r\n", "\n")
            for idx, block in enumerate(op.blocks):
                rep_lines = len(block.replace.splitlines())
                if rep_lines > self._max_chunk_lines:
                    raise PatchError(
                        f"SEARCH_REPLACE block #{idx} in '{op.path}' exceeds "
                        f"{self._max_chunk_lines} lines ({rep_lines})"
                    )
                needle = block.search.replace("\r\n", "\n")
                occurrences = text.count(needle)
                if occurrences != 1:
                    raise AmbiguousPatch(
                        f"SEARCH_REPLACE block #{idx} in '{op.path}' matched {occurrences} "
                        "locations (expected exactly 1)"
                    )
                text = text.replace(needle, block.replace.replace("\r\n", "\n"), 1)
            return text

        # LineRangeOp
        if op.end_line < op.start_line:
            raise PatchError(f"Invalid line range {op.start_line}..{op.end_line} in '{op.path}'")
        rep_lines_list = op.replacement.replace("\r\n", "\n").splitlines()
        if len(rep_lines_list) > self._max_chunk_lines:
            raise PatchError(
                f"LINE_RANGE replacement in '{op.path}' exceeds {self._max_chunk_lines} lines"
            )
        lines = current.replace("\r\n", "\n").splitlines()
        if op.end_line > len(lines):
            raise PatchError(
                f"LINE_RANGE {op.start_line}..{op.end_line} out of bounds ({len(lines)} lines)"
            )
        target_slice = lines[op.start_line - 1 : op.end_line]
        actual_hash = compute_lines_sha256(target_slice)
        if actual_hash != op.expected_sha256:
            raise StaleContextHashError(
                f"LINE_RANGE hash mismatch in '{op.path}': expected {op.expected_sha256}, "
                f"actual {actual_hash}"
            )
        merged = [*lines[: op.start_line - 1], *rep_lines_list, *lines[op.end_line :]]
        trailing = "\n" if current.endswith("\n") else ""
        return "\n".join(merged) + trailing

    def _validate_python_syntax(self, content: str, *, rel_path: str) -> None:
        try:
            ast.parse(content, filename=rel_path)
        except SyntaxError as exc:
            raise PatchSyntaxError(
                f"Patched Python file '{rel_path}' failed AST syntax check at line "
                f"{exc.lineno}: {exc.msg}"
            ) from exc

    def _scan_existing_case_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for p in self.workspace_dir.rglob("*"):
            if any(part.startswith(".git") or part == "__pycache__" for part in p.parts):
                continue
            if p.is_file():
                rel = normalize_rel_posix_path(str(p.relative_to(self.workspace_dir)))
                mapping[rel.casefold()] = rel
        return mapping
