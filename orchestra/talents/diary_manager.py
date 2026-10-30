"""Discovery Diary manager enforcing Empirical Proof Gate before VERIFIED promotion (INV-03)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestra.config.constants import DIARY_INJECT_TOKENS
from orchestra.errors import DiaryGateViolation
from orchestra.providers.token_counter import count_tokens_sync

if TYPE_CHECKING:
    from pathlib import Path

DiaryStatus = Literal["HYPOTHESIS", "VERIFIED", "REFUTED"]


class EmpiricalProof(BaseModel):
    """Objective sandbox evidence required to promote a diary entry to VERIFIED (TL-07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    exit_code: int
    artifact_sha256: str
    observed_summary: str
    verified_by_judge: bool = True


class DiaryEntry(BaseModel):
    """Single engineering discovery or hypothesis recorded by an agent (§22.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"diary_{uuid.uuid4().hex[:10]}")
    role_id: str
    project_id: str
    node_id: str | None = None
    status: DiaryStatus = "HYPOTHESIS"
    observation: str
    lesson: str
    tags: tuple[str, ...] = ()
    proof: EmpiricalProof | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _enforce_empirical_proof_gate(self) -> DiaryEntry:
        """INV-03: Never allow VERIFIED status without concrete EmpiricalProof!"""
        if self.status == "VERIFIED":
            if self.proof is None:
                raise DiaryGateViolation(
                    "Cannot create VERIFIED DiaryEntry without EmpiricalProof (INV-03)"
                )
            if not self.proof.command.strip() or not self.proof.artifact_sha256.strip():
                raise DiaryGateViolation(
                    "EmpiricalProof must contain non-empty command and artifact_sha256"
                )
            if self.proof.exit_code != 0 or not self.proof.verified_by_judge:
                raise DiaryGateViolation(
                    "VERIFIED DiaryEntry requires exit_code==0 and verified_by_judge==True"
                )
        return self


class DiaryManager:
    """Append-only JSONL diary store under `runtime/agent_talents/<role_id>/diary.jsonl`."""

    def __init__(self, talents_dir: Path) -> None:
        """Initialize DiaryManager."""
        self._dir = talents_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _diary_file(self, role_id: str) -> Path:
        rdir = self._dir / role_id
        rdir.mkdir(parents=True, exist_ok=True)
        return rdir / "diary.jsonl"

    def append(self, entry: DiaryEntry) -> DiaryEntry:
        """Validate Empirical Proof Gate and append `entry` to role's `diary.jsonl`."""
        path = self._diary_file(entry.role_id)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    def promote_hypothesis(self, entry: DiaryEntry, proof: EmpiricalProof) -> DiaryEntry:
        """Promote a HYPOTHESIS entry to VERIFIED once empirical proof is obtained."""
        promoted = entry.model_copy(update={"status": "VERIFIED", "proof": proof})
        return self.append(promoted)

    def read_entries(
        self,
        role_id: str,
        *,
        status: DiaryStatus | None = None,
    ) -> list[DiaryEntry]:
        """Load diary entries for `role_id`, optionally filtered by `status`."""
        path = self._diary_file(role_id)
        if not path.is_file():
            return []
        entries: dict[str, DiaryEntry] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            item = DiaryEntry.model_validate_json(stripped)
            entries[item.id] = item
        vals = list(entries.values())
        if status is not None:
            vals = [e for e in vals if e.status == status]
        return vals

    def render_verified_for_prompt(
        self,
        role_id: str,
        *,
        tags: set[str] | None = None,
        max_tokens: int = DIARY_INJECT_TOKENS,
    ) -> str:
        """Render ONLY `VERIFIED` entries (never unverified hypotheses) up to `max_tokens`."""
        verified = self.read_entries(role_id, status="VERIFIED")
        tag_filter = {t.lower() for t in tags} if tags else set()
        verified.sort(
            key=lambda e: (
                len({t.lower() for t in e.tags} & tag_filter) if tag_filter else 0,
                e.created_at,
            ),
            reverse=True,
        )
        lines: list[str] = []
        for entry in verified:
            line = f"- [{entry.id}] {entry.lesson}"
            candidate = "\n".join([*lines, line])
            if count_tokens_sync(candidate) > max_tokens:
                break
            lines.append(line)
        return "\n".join(lines) if lines else "NONE"
