"""Stage S10 Post-Mortem distiller evolving `/agent_talents/` and project antipatterns (§16.1)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from orchestra.state.antipatterns import AntipatternEntry
from orchestra.talents.diary_manager import DiaryManager
from orchestra.talents.talents_store import TalentsStore

if TYPE_CHECKING:
    from orchestra.state.mental_map import MentalMap


class PostMortemReport(BaseModel):
    """Structured summary of S10 post-mortem learning distillation (TL-11, TL-12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    roles_evolved: tuple[str, ...]
    new_antipatterns: tuple[AntipatternEntry, ...]
    retried_nodes: tuple[str, ...]
    summary_ru: str


class PostMortemAnalyzer:
    """Distills verified diary entries and retry patterns into TalentsStore & Antipatterns."""

    def __init__(
        self,
        *,
        talents_store: TalentsStore,
        diary_manager: DiaryManager,
    ) -> None:
        """Initialize PostMortemAnalyzer."""
        self._talents = talents_store
        self._diary = diary_manager

    def run_post_mortem(
        self,
        mental_map: MentalMap,
        *,
        participating_roles: set[str],
    ) -> PostMortemReport:
        """Evolve participating role talents using ONLY `VERIFIED` diary lessons (INV-03)."""
        evolved: list[str] = []
        synthesized_aps: list[AntipatternEntry] = []

        retried = tuple(nid for nid, n in sorted(mental_map.nodes.items()) if n.retry_count > 0)

        for role_id in sorted(participating_roles):
            verified_entries = [
                e
                for e in self._diary.read_entries(role_id, status="VERIFIED")
                if e.project_id == mental_map.project_id
            ]
            lessons = [e.lesson for e in verified_entries]
            self._talents.evolve_profile(
                role_id,
                new_heuristics=lessons,
                project_completed=True,
            )
            evolved.append(role_id)

            for entry in verified_entries:
                if "avoid" in entry.lesson.lower() or "never" in entry.lesson.lower():
                    synthesized_aps.append(
                        AntipatternEntry(
                            id=f"AP-{uuid.uuid4().hex[:6].upper()}",
                            symptom=entry.observation,
                            root_cause="Distilled from verified empirical diary entry",
                            forbidden_pattern=entry.observation[:100],
                            safe_alternative=entry.lesson,
                            severity="HIGH",
                            tags=entry.tags,
                            node_id=entry.node_id,
                        )
                    )

        summary_ru = (
            f"Пост-мортем проекта «{mental_map.project_id}»: обновлено профилей талантов — "
            f"{len(evolved)}, узлов с повторными попытками — {len(retried)}, "
            f"новых антипаттернов — {len(synthesized_aps)}."
        )
        return PostMortemReport(
            project_id=mental_map.project_id,
            roles_evolved=tuple(evolved),
            new_antipatterns=tuple(synthesized_aps),
            retried_nodes=retried,
            summary_ru=summary_ru,
        )
