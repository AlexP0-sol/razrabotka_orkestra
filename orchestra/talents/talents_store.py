"""Persistent `/agent_talents/` store preserving evolved role expertise across projects (§22.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from orchestra.agents.charter import AgentCharter
from orchestra.state.event_log import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


class TalentProfile(BaseModel):
    """Cross-project accumulated talent profile for a specialist role (TL-01..TL-06)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: str
    version: int = Field(default=1, ge=1)
    display_name: str
    tags: tuple[str, ...] = ()
    system_traits: tuple[str, ...] = ()
    proven_heuristics: tuple[str, ...] = ()
    known_pitfalls: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    forbidden_libs: tuple[str, ...] = ()
    style_rules: tuple[str, ...] = ()
    projects_completed: int = 0
    nodes_passed_first_try: int = 0
    nodes_total: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def first_pass_rate(self) -> float:
        """Compute empirical first-pass verification rate in [0.0, 1.0]."""
        if self.nodes_total <= 0:
            return 1.0
        return round(self.nodes_passed_first_try / self.nodes_total, 4)


class TalentsStore:
    """Filesystem-backed repository under `runtime/agent_talents/<role_id>/` (ST-02, TL-01)."""

    def __init__(self, talents_dir: Path) -> None:
        """Initialize TalentsStore directory."""
        self._dir = talents_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def role_dir(self, role_id: str) -> Path:
        """Return directory path for `role_id`."""
        return self._dir / role_id

    def load(self, role_id: str) -> TalentProfile | None:
        """Load persisted TalentProfile for `role_id` if present."""
        profile_path = self.role_dir(role_id) / "profile.json"
        if not profile_path.is_file():
            return None
        return TalentProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))

    def save(self, profile: TalentProfile, *, charter: AgentCharter | None = None) -> TalentProfile:
        """Atomically save TalentProfile (with version backup) and optional Charter snapshot."""
        rdir = self.role_dir(profile.role_id)
        rdir.mkdir(parents=True, exist_ok=True)
        profile_path = rdir / "profile.json"
        if profile_path.is_file():
            backup_dir = rdir / "history"
            backup_dir.mkdir(parents=True, exist_ok=True)
            old_data = json.loads(profile_path.read_text(encoding="utf-8"))
            old_ver = int(old_data.get("version", 1))
            atomic_write_text(
                backup_dir / f"profile.v{old_ver}.json",
                json.dumps(old_data, indent=2),
            )
        atomic_write_text(profile_path, profile.model_dump_json(indent=2))
        if charter is not None:
            atomic_write_text(rdir / "charter.json", charter.model_dump_json(indent=2))
        return profile

    def hire_or_create(self, charter: AgentCharter) -> TalentProfile:
        """Load existing evolved TalentProfile for `charter.role_id` or bootstrap v1 (TL-02)."""
        existing = self.load(charter.role_id)
        if existing is not None:
            return existing
        created = TalentProfile(
            role_id=charter.role_id,
            version=1,
            display_name=charter.display_name,
            tags=(*charter.owned_node_types, charter.tier),
            system_traits=charter.responsibilities,
        )
        return self.save(created, charter=charter)

    def evolve_profile(
        self,
        role_id: str,
        *,
        new_heuristics: list[str] | tuple[str, ...] = (),
        new_pitfalls: list[str] | tuple[str, ...] = (),
        preferred_skills: list[str] | tuple[str, ...] = (),
        nodes_passed_delta: int = 0,
        nodes_total_delta: int = 0,
        project_completed: bool = False,
    ) -> TalentProfile:
        """Apply verified post-mortem lessons and bump profile version (TL-05)."""
        current = self.load(role_id)
        if current is None:
            current = TalentProfile(role_id=role_id, display_name=role_id)

        merged_h = tuple(dict.fromkeys((*current.proven_heuristics, *new_heuristics)))
        merged_p = tuple(dict.fromkeys((*current.known_pitfalls, *new_pitfalls)))
        merged_s = tuple(dict.fromkeys((*current.preferred_skills, *preferred_skills)))
        updated = current.model_copy(
            update={
                "version": current.version + 1,
                "proven_heuristics": merged_h,
                "known_pitfalls": merged_p,
                "preferred_skills": merged_s,
                "nodes_passed_first_try": current.nodes_passed_first_try
                + max(0, nodes_passed_delta),
                "nodes_total": current.nodes_total + max(0, nodes_total_delta),
                "projects_completed": current.projects_completed + (1 if project_completed else 0),
                "updated_at": datetime.now(UTC),
            }
        )
        return self.save(updated)

    def match_by_tags(self, required_tags: set[str]) -> list[TalentProfile]:
        """Rank all persisted talent profiles by tag overlap and empirical first-pass rate."""
        wanted = {t.lower() for t in required_tags}
        profiles: list[TalentProfile] = []
        for child in sorted(self._dir.iterdir()):
            if child.is_dir():
                loaded = self.load(child.name)
                if loaded is not None:
                    profiles.append(loaded)
        profiles.sort(
            key=lambda p: (
                len({t.lower() for t in p.tags} & wanted),
                p.first_pass_rate,
                p.projects_completed,
            ),
            reverse=True,
        )
        return profiles
