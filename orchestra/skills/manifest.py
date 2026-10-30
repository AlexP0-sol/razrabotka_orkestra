"""Skill manifest schemas, compact prompt index, and auto-deprecation rules (§20.1, SK-01..08)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import (
    SKILL_DEPRECATE_MIN_CALLS,
    SKILL_DEPRECATE_SUCCESS_RATE,
    SKILL_INDEX_INJECT_TOKENS,
)
from orchestra.providers.token_counter import count_tokens_sync


class SkillSpec(BaseModel):
    """Formal metadata and JSON contracts for one executable skill in `/skills/`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    version: str = "1.0.0"
    description: str
    tags: tuple[str, ...] = ()
    entrypoint_path: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_s: float = Field(default=30.0, gt=0.1, le=300.0)
    builtin: bool = False
    deprecated: bool = False
    calls_total: int = 0
    calls_succeeded: int = 0
    sha256: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def success_rate(self) -> float:
        """Return empirical invocation success ratio in [0.0, 1.0]."""
        if self.calls_total <= 0:
            return 1.0
        return round(self.calls_succeeded / self.calls_total, 4)

    def record_invocation(self, *, succeeded: bool) -> SkillSpec:
        """Update counters and auto-deprecate if success_rate < 0.5 after >= 20 calls (SK-08)."""
        total = self.calls_total + 1
        succ = self.calls_succeeded + (1 if succeeded else 0)
        rate = succ / total
        auto_dep = self.deprecated or (
            total >= SKILL_DEPRECATE_MIN_CALLS and rate < SKILL_DEPRECATE_SUCCESS_RATE
        )
        return self.model_copy(
            update={
                "calls_total": total,
                "calls_succeeded": succ,
                "deprecated": auto_dep,
                "updated_at": datetime.now(UTC),
            }
        )


class SkillManifest(BaseModel):
    """Root `/skills/manifest.json` catalog persisted across projects (ST-02, SK-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    skills: dict[str, SkillSpec] = Field(default_factory=dict)

    def render_compact_index(
        self,
        *,
        tags: set[str] | None = None,
        max_tokens: int = SKILL_INDEX_INJECT_TOKENS,
    ) -> str:
        """Render non-deprecated skill index bounded by `SKILL_INDEX_INJECT_TOKENS` (§25)."""
        tag_filter = {t.lower() for t in tags} if tags else set()
        active = [s for s in self.skills.values() if not s.deprecated]
        active.sort(
            key=lambda s: (
                len({t.lower() for t in s.tags} & tag_filter) if tag_filter else 0,
                s.success_rate,
                s.id,
            ),
            reverse=True,
        )
        lines: list[str] = []
        for spec in active:
            line = f"- {spec.id}@v{spec.version}: {spec.description}"
            candidate = "\n".join([*lines, line])
            if count_tokens_sync(candidate) > max_tokens:
                break
            lines.append(line)
        return "\n".join(lines) if lines else "NONE"
