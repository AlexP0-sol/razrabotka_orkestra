"""Dynamic Swarm Organization Designer proposing optimal agent rosters (§13.7, §16.1 S3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from orchestra.agents.charter import AgentCharter
from orchestra.agents.roles_catalog import (
    CRYPTO_ROLE_TEMPLATES,
    PERMANENT_ROLE_TEMPLATES,
    instantiate_charter,
    validate_team_roster,
)
from orchestra.config.constants import MAX_AGENTS_HARD_CAP, MAX_CONCURRENT_AGENTS
from orchestra.state.mental_map import MentalMap


class TeamProposal(BaseModel):
    """Proposed swarm team composition presented at Gate G0 (SW-01..SW-06)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    domain: str
    charters: tuple[AgentCharter, ...]
    estimated_parallelism: int
    rationale_ru: str

    def format_sitrep_ru(self) -> str:
        """Render concise Russian team proposal card for human operator approval (LP-03)."""
        lines = [
            f"Состав роя для проекта «{self.project_id}» (домен: {self.domain}, "
            f"агентов: {len(self.charters)}, параллелизм: до {self.estimated_parallelism}):",
        ]
        for ch in self.charters:
            lines.append(f"- `{ch.agent_id}` — {ch.display_name} [{ch.tier}]")
        lines.append(f"Обоснование: {self.rationale_ru}")
        return "\n".join(lines)


class OrgDesigner:
    """Synthesizes minimum-sufficient specialist teams tailored to MentalMap topology."""

    def __init__(
        self,
        *,
        max_concurrent_agents: int = MAX_CONCURRENT_AGENTS,
        hard_cap: int = MAX_AGENTS_HARD_CAP,
    ) -> None:
        """Initialize OrgDesigner with concurrency and roster caps."""
        self._max_concurrent = max_concurrent_agents
        self._hard_cap = hard_cap

    def design_team(self, mental_map: MentalMap) -> TeamProposal:
        """Build validated TeamProposal for `mental_map` (SW-01..SW-06)."""
        roster: list[AgentCharter] = [
            instantiate_charter(role_id, instance_index=1) for role_id in PERMANENT_ROLE_TEMPLATES
        ]

        code_nodes = [n for n in mental_map.nodes.values() if n.type == "code"]
        has_research = any(n.type == "research" for n in mental_map.nodes.values())
        has_docs = any(n.type == "docs" for n in mental_map.nodes.values())
        has_ui = any(
            any(ext in f for ext in (".html", ".css", ".js"))
            for n in mental_map.nodes.values()
            for f in n.owned_files
        )

        if mental_map.domain == "crypto_trading":
            for role_id in CRYPTO_ROLE_TEMPLATES:
                if len(roster) < self._hard_cap:
                    roster.append(instantiate_charter(role_id, instance_index=1))
        else:
            dev_workers = max(1, min(self._max_concurrent - 1, max(1, len(code_nodes) // 2 + 1)))
            for idx in range(1, dev_workers + 1):
                if len(roster) < self._hard_cap:
                    roster.append(instantiate_charter("core_developer", instance_index=idx))
            if has_ui and len(roster) < self._hard_cap:
                roster.append(instantiate_charter("frontend_ui_developer", instance_index=1))
            if has_research and len(roster) < self._hard_cap:
                roster.append(instantiate_charter("research_specialist", instance_index=1))
            if has_docs and len(roster) < self._hard_cap:
                roster.append(instantiate_charter("docs_writer", instance_index=1))

        # Ensure any explicit owner_role declared on MentalMap nodes is represented
        existing_roles = {c.role_id for c in roster}
        for node in mental_map.nodes.values():
            role = node.owner_role
            if role == "developer":
                continue
            if role not in existing_roles and len(roster) < self._hard_cap:
                try:
                    roster.append(instantiate_charter(role, instance_index=1))
                    existing_roles.add(role)
                except Exception:
                    continue

        validate_team_roster(roster, hard_cap=self._hard_cap)
        parallelism = min(self._max_concurrent, max(1, len(code_nodes)))
        rationale = (
            f"Сформировано 4 постоянных ядровых агента и {len(roster) - 4} профильных "
            f"исполнителя под {len(mental_map.nodes)} узлов графа (домен '{mental_map.domain}')."
        )
        return TeamProposal(
            project_id=mental_map.project_id,
            domain=mental_map.domain,
            charters=tuple(roster),
            estimated_parallelism=parallelism,
            rationale_ru=rationale,
        )
