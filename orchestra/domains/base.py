"""Base protocol and models for pluggable ORCHESTRA Domain Packs (Part VIII, §38)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pathlib import Path

    from orchestra.skills.registry import SkillRegistry
    from orchestra.state.mental_map import MentalMap
    from orchestra.verify.verifiers import VerifierFinding


class DomainRoleBlueprint(BaseModel):
    """Domain-specialized talent blueprint seeded with empty `DISCOVERY_DIARY.md` (DP-54..55)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: str
    title: str
    domain_tags: tuple[str, ...]
    preferred_stack: tuple[str, ...]
    forbidden_libs: tuple[str, ...]
    style_rules: tuple[str, ...]


class EscalationQuestionTemplate(BaseModel):
    """Mandatory business question that Architect must escalate to human operator (DP-69..70)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    blocks_s4: bool
    question_ru: str
    options_ru: tuple[str, ...]
    recommendation_ru: str


class DomainPack(Protocol):
    """Pluggable domain pack equipping the factory with professions, skills, and verifiers."""

    @property
    def domain_id(self) -> str:
        """Canonical domain identifier (e.g., `'crypto_trading'`)."""
        ...

    def role_blueprints(self) -> tuple[DomainRoleBlueprint, ...]:
        """Return default domain talent professions."""
        ...

    def skill_ids(self) -> tuple[str, ...]:
        """Return built-in domain skill identifiers."""
        ...

    def mandatory_escalations(self) -> tuple[EscalationQuestionTemplate, ...]:
        """Return mandatory business escalation templates."""
        ...

    def seed_talents(self, talents_dir: Path) -> list[str]:
        """Seed `profile.json` + empty `DISCOVERY_DIARY.md` for each domain profession."""
        ...

    def seed_skills(self, skills_dir: Path, registry: SkillRegistry | None = None) -> list[str]:
        """Seed `skill.yaml`, `skill.py`, and `test_skill.py` into `skills_dir`."""
        ...

    def verify_files(self, workspace_root: Path, rel_paths: list[str]) -> list[VerifierFinding]:
        """Run domain-specific AST/static checks over generated artifacts."""
        ...

    def build_reference_map(self, *, project_id: str, project_name: str) -> MentalMap:
        """Construct the canonical reference `MentalMap` decomposition for the domain."""
        ...
