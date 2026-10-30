"""Project-level antipattern registry and prompt selection helpers (§13.5, MM-09, §25)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import ANTIPATTERN_INJECT_LIMIT

AntipatternSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

_SEVERITY_RANK: dict[AntipatternSeverity, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


class AntipatternEntry(BaseModel):
    """Verified failure pattern banned from future code generation in the project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    symptom: str
    root_cause: str
    forbidden_pattern: str
    safe_alternative: str
    severity: AntipatternSeverity = "HIGH"
    occurrences: int = Field(default=1, ge=1)
    tags: tuple[str, ...] = ()
    node_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def select_relevant_antipatterns(
    entries: tuple[AntipatternEntry, ...] | list[AntipatternEntry],
    *,
    tags: set[str] | None = None,
    limit: int = ANTIPATTERN_INJECT_LIMIT,
) -> list[AntipatternEntry]:
    """Select up to `limit` highest-priority antipatterns matching optional `tags`."""
    tag_filter = {t.lower() for t in tags} if tags else set()

    def _score(entry: AntipatternEntry) -> tuple[int, int, int, str]:
        entry_tags = {t.lower() for t in entry.tags}
        overlap = len(entry_tags & tag_filter) if tag_filter else 0
        return (
            1 if overlap > 0 else 0,
            _SEVERITY_RANK[entry.severity],
            entry.occurrences,
            entry.id,
        )

    ranked = sorted(entries, key=_score, reverse=True)
    return ranked[: max(0, limit)]


def format_antipatterns_compact(entries: list[AntipatternEntry]) -> str:
    """Format antipatterns concisely for system prompt injection (§25)."""
    if not entries:
        return "NONE"
    lines = [
        f"[{e.id}|{e.severity}] AVOID: {e.forbidden_pattern} -> USE: {e.safe_alternative}"
        for e in entries
    ]
    return "\n".join(lines)
