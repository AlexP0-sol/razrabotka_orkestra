"""Architecture Decision Record (ADR) models and Markdown serialization (§13.5, MM-08)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AdrStatus = Literal["PROPOSED", "ACCEPTED", "SUPERSEDED", "DEPRECATED"]


class ArchitectureDecisionRecord(BaseModel):
    """Immutable Architecture Decision Record stored in MentalMap and exported to Markdown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    status: AdrStatus = "ACCEPTED"
    context: str
    decision: str
    consequences: str
    alternatives: tuple[str, ...] = ()
    superseded_by: str | None = None
    author_agent: str = "architect"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_markdown(self) -> str:
        """Render this ADR into canonical Markdown format (§37.3)."""
        alt_lines = "\n".join(f"- {item}" for item in self.alternatives) or "- None recorded"
        sup_line = f"\n- **Superseded By**: `{self.superseded_by}`" if self.superseded_by else ""
        date_str = self.created_at.strftime("%Y-%m-%d")
        return (
            f"# {self.id}: {self.title}\n\n"
            f"- **Status**: {self.status}\n"
            f"- **Date**: {date_str}\n"
            f"- **Author**: `{self.author_agent}`{sup_line}\n\n"
            f"## Context\n{self.context}\n\n"
            f"## Decision\n{self.decision}\n\n"
            f"## Alternatives Considered\n{alt_lines}\n\n"
            f"## Consequences\n{self.consequences}\n"
        )
