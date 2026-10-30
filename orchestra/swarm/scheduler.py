"""Critical-path DAG scheduler, agent matching, and deadlock/starvation detector (§13.7)."""

from __future__ import annotations

from dataclasses import dataclass

from orchestra.agents.charter import AgentCharter
from orchestra.config.constants import MAX_CONCURRENT_AGENTS
from orchestra.state.mental_map import MapNode, MentalMap


@dataclass(frozen=True)
class ScheduledAssignment:
    """Pairing of a ready MapNode and an idle AgentCharter selected for execution."""

    node: MapNode
    agent: AgentCharter
    critical_path_depth: int


class CriticalPathScheduler:
    """Ranks READY nodes by critical-path length and matches them to idle chartered agents."""

    def __init__(self, *, max_concurrent_agents: int = MAX_CONCURRENT_AGENTS) -> None:
        """Initialize CriticalPathScheduler."""
        self._max_concurrent = max(1, max_concurrent_agents)

    def compute_critical_depths(self, mental_map: MentalMap) -> dict[str, int]:
        """Compute longest downstream dependency chain length from every node in DAG (SW-07)."""
        topo = mental_map.topological_order()
        dependents: dict[str, list[str]] = {nid: [] for nid in mental_map.nodes}
        for nid, node in mental_map.nodes.items():
            for dep in node.dependencies:
                if dep in dependents:
                    dependents[dep].append(nid)

        depths: dict[str, int] = dict.fromkeys(topo, 1)
        for nid in reversed(topo):
            children = dependents[nid]
            if children:
                depths[nid] = 1 + max(depths[ch] for ch in children)
        return depths

    def schedule(
        self,
        mental_map: MentalMap,
        *,
        roster: tuple[AgentCharter, ...] | list[AgentCharter],
        busy_agent_ids: set[str],
    ) -> list[ScheduledAssignment]:
        """Return ordered `(node, agent)` assignments respecting concurrency limits (SW-08)."""
        available_slots = self._max_concurrent - len(busy_agent_ids)
        if available_slots <= 0:
            return []

        depths = self.compute_critical_depths(mental_map)
        ready_candidates = [
            node
            for node in mental_map.nodes.values()
            if node.status in ("READY", "NEEDS_REWORK")
            and all(mental_map.nodes[d].status == "DONE" for d in node.dependencies)
        ]
        ready_candidates.sort(key=lambda n: (-depths.get(n.id, 1), n.priority, n.retry_count, n.id))

        idle_agents = [
            ch for ch in roster if ch.agent_id not in busy_agent_ids and not ch.can_approve_patches
        ]

        assignments: list[ScheduledAssignment] = []
        used_agents: set[str] = set()
        for node in ready_candidates:
            if len(assignments) >= available_slots:
                break
            matched = self._match_agent(node, idle_agents, used_agents)
            if matched is not None:
                used_agents.add(matched.agent_id)
                assignments.append(
                    ScheduledAssignment(
                        node=node,
                        agent=matched,
                        critical_path_depth=depths.get(node.id, 1),
                    )
                )
        return assignments

    def _match_agent(
        self,
        node: MapNode,
        idle_agents: list[AgentCharter],
        used_agents: set[str],
    ) -> AgentCharter | None:
        # 1) Exact role_id match
        for ch in idle_agents:
            if ch.agent_id not in used_agents and ch.role_id == node.owner_role:
                return ch
        # 2) Alias 'developer' -> any developer / domain coder owning node.type
        for ch in idle_agents:
            if ch.agent_id not in used_agents and node.type in ch.owned_node_types:
                return ch
        # 3) Fallback to any non-judge worker
        for ch in idle_agents:
            if ch.agent_id not in used_agents:
                return ch
        return None

    def detect_deadlock(
        self,
        mental_map: MentalMap,
        *,
        active_task_count: int,
    ) -> str | None:
        """Return diagnostic message if unfinished nodes exist with zero progress paths (SW-09)."""
        if active_task_count > 0:
            return None
        unfinished = [n for n in mental_map.nodes.values() if n.status not in ("DONE", "CANCELLED")]
        if not unfinished:
            return None
        if any(n.status == "BLOCKED_HUMAN" for n in unfinished):
            return None
        ready = [
            n
            for n in unfinished
            if n.status in ("READY", "NEEDS_REWORK")
            and all(mental_map.nodes[d].status == "DONE" for d in n.dependencies)
        ]
        if ready:
            return None
        summary = ", ".join(f"{n.id}:{n.status}" for n in unfinished[:8])
        return (
            f"Swarm deadlock detected: no active tasks and no schedulable READY nodes ({summary})"
        )
