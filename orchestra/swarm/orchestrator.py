"""End-to-end Swarm Orchestrator driving stages S0..S14 with parallel build & judge loop (§16.1)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from orchestra.agents.agent_runtime import AgentExecutionResult, AgentRuntime
from orchestra.agents.charter import AgentCharter, assert_independent_reviewer
from orchestra.config.constants import MAX_CONCURRENT_AGENTS, MAX_RETRIES, SHUTDOWN_GRACE_S
from orchestra.errors import BudgetExceeded, StateError
from orchestra.state.mental_map import MapNode
from orchestra.swarm.gates import GateVerdict, evaluate_g2_node_verification
from orchestra.swarm.org_designer import OrgDesigner, TeamProposal
from orchestra.swarm.scheduler import CriticalPathScheduler
from orchestra.talents.talents_store import TalentsStore

if TYPE_CHECKING:
    from pydantic import BaseModel

    from orchestra.inference.llm_client import DualStreamLLMRuntime
    from orchestra.state.mental_map import MentalMap
    from orchestra.state.state_store import StateStore

PipelineStage = Literal[
    "S0_INTAKE",
    "S1_PRE_STUDY",
    "S2_ARCHITECTURE",
    "S3_TEAM_DESIGN",
    "S4_CONTRACTS_FREEZE",
    "S5_TEST_MATRIX",
    "S6_PARALLEL_BUILD",
    "S7_NODE_VERIFY",
    "S8_INTEGRATION",
    "S9_ADVERSARIAL_QA",
    "S10_POST_MORTEM",
    "S11_DOCS_RUNBOOK",
    "S12_FINAL_GATE",
    "S13_DELIVERY",
    "S14_PAUSED_OR_ABORTED",
]


@dataclass(frozen=True)
class JudgeEvaluation:
    """Result returned by the Independent Judge callback for a VERIFYING node."""

    passed: bool
    defects: tuple[str, ...] = ()


NodeVerifyCallback = Callable[
    [MapNode, AgentExecutionResult, AgentCharter], Awaitable[JudgeEvaluation]
]


class SwarmRunSummary:
    """Execution telemetry and terminal status of a SwarmOrchestrator build cycle."""

    def __init__(
        self,
        *,
        final_map: MentalMap,
        completed_nodes: tuple[str, ...],
        blocked_nodes: tuple[str, ...],
        wallclock_s: float,
    ) -> None:
        """Initialize SwarmRunSummary."""
        self.final_map = final_map
        self.completed_nodes = completed_nodes
        self.blocked_nodes = blocked_nodes
        self.wallclock_s = wallclock_s


class SwarmOrchestrator:
    """Coordinates parallel chartered agents, Independent Judge checks, and budgets (§13.7)."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        llm_runtime: DualStreamLLMRuntime,
        talents_store: TalentsStore,
        verify_callback: NodeVerifyCallback | None = None,
        node_response_schema: type[BaseModel] | None = None,
        max_concurrent_agents: int = MAX_CONCURRENT_AGENTS,
        max_retries_per_node: int = MAX_RETRIES,
        project_token_limit: int | None = None,
        project_wallclock_s: float | None = None,
        shutdown_grace_s: float = SHUTDOWN_GRACE_S,
    ) -> None:
        """Initialize SwarmOrchestrator."""
        self._store = state_store
        self._llm = llm_runtime
        self._talents = talents_store
        self._verify_cb = verify_callback or _default_pass_verifier
        self._node_response_schema = node_response_schema
        self._max_concurrent = max(1, max_concurrent_agents)
        self._max_retries = max(0, max_retries_per_node)
        self._project_token_limit = project_token_limit
        self._project_wallclock_s = project_wallclock_s
        self._shutdown_grace_s = shutdown_grace_s

        self._org_designer = OrgDesigner(max_concurrent_agents=self._max_concurrent)
        self._scheduler = CriticalPathScheduler(max_concurrent_agents=self._max_concurrent)
        self._runtimes: dict[str, AgentRuntime] = {}
        self._roster: tuple[AgentCharter, ...] = ()
        self._stop_event = asyncio.Event()

    @property
    def roster(self) -> tuple[AgentCharter, ...]:
        """Return currently hired agent charters."""
        return self._roster

    def hire_team(self, proposal: TeamProposal | None = None) -> TeamProposal:
        """Hire agents from `proposal` (or auto-design from MentalMap) and bind TalentsStore."""
        chosen = proposal or self._org_designer.design_team(self._store.snapshot())
        self._roster = chosen.charters
        self._runtimes.clear()
        for charter in self._roster:
            talent = self._talents.hire_or_create(charter)
            self._runtimes[charter.agent_id] = AgentRuntime(
                charter=charter,
                talent=talent,
                llm_runtime=self._llm,
                state_store=self._store,
            )
        return chosen

    def request_shutdown(self) -> None:
        """Signal graceful stop so in-flight nodes finish or checkpoint cleanly."""
        self._stop_event.set()

    async def run_build_loop(self) -> SwarmRunSummary:
        """Execute stages S6/S7 parallel build & independent verification until completion."""
        if not self._roster:
            self.hire_team()

        judge = next((c for c in self._roster if c.can_approve_patches), None)
        if judge is None:
            raise StateError(
                "Swarm roster lacks an Independent Judge with can_approve_patches=True"
            )

        await self._store.mutate(
            actor="orchestrator",
            mutation_type="set_stage",
            payload={"stage": "S6_PARALLEL_BUILD"},
        )

        start_t = time.monotonic()
        busy_agents: set[str] = set()
        in_flight: dict[asyncio.Task[None], str] = {}

        try:
            while not self._stop_event.is_set():
                snap = self._store.snapshot()
                self._check_project_budgets(snap, elapsed_s=time.monotonic() - start_t)

                # Normalize NEEDS_REWORK -> READY when ready to retry
                for node in snap.nodes.values():
                    if node.status == "NEEDS_REWORK":
                        snap = await self._store.mutate(
                            actor="orchestrator",
                            mutation_type="transition_node",
                            payload={"node_id": node.id, "status": "READY"},
                        )

                assignments = self._scheduler.schedule(
                    snap,
                    roster=self._roster,
                    busy_agent_ids=busy_agents,
                )
                for item in assignments:
                    busy_agents.add(item.agent.agent_id)
                    task = asyncio.create_task(
                        self._execute_and_verify_node(
                            node_id=item.node.id,
                            worker=item.agent,
                            judge=judge,
                        ),
                        name=f"worker-{item.agent.agent_id}-{item.node.id}",
                    )
                    in_flight[task] = item.agent.agent_id

                if not in_flight:
                    snap = self._store.snapshot()
                    deadlock_msg = self._scheduler.detect_deadlock(snap, active_task_count=0)
                    if deadlock_msg is not None:
                        raise StateError(deadlock_msg)
                    break

                done, _ = await asyncio.wait(
                    in_flight.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for finished in done:
                    aid = in_flight.pop(finished)
                    busy_agents.discard(aid)
                    await finished
        finally:
            if in_flight:
                _, pending = await asyncio.wait(
                    in_flight.keys(),
                    timeout=self._shutdown_grace_s,
                )
                for p_task in pending:
                    p_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            await self._store.checkpoint()

        final_snap = self._store.snapshot()
        completed = tuple(nid for nid, n in sorted(final_snap.nodes.items()) if n.status == "DONE")
        blocked = tuple(
            nid
            for nid, n in sorted(final_snap.nodes.items())
            if n.status in ("BLOCKED_HUMAN", "FAILED")
        )
        next_stage: PipelineStage = "S8_INTEGRATION" if not blocked else "S14_PAUSED_OR_ABORTED"
        final_snap = await self._store.mutate(
            actor="orchestrator",
            mutation_type="set_stage",
            payload={"stage": next_stage},
        )
        return SwarmRunSummary(
            final_map=final_snap,
            completed_nodes=completed,
            blocked_nodes=blocked,
            wallclock_s=round(time.monotonic() - start_t, 3),
        )

    async def _execute_and_verify_node(
        self,
        *,
        node_id: str,
        worker: AgentCharter,
        judge: AgentCharter,
    ) -> None:
        assert_independent_reviewer(author_agent_id=worker.agent_id, reviewer_charter=judge)
        _, lease = await self._store.acquire_node_lease(node_id=node_id, agent_id=worker.agent_id)
        runtime = self._runtimes[worker.agent_id]
        node_snap = self._store.snapshot().nodes[node_id]

        defect_note = (
            "\nFix prior defects: " + "; ".join(node_snap.rework_defects)
            if node_snap.rework_defects
            else ""
        )
        schema_hint = (
            " Return JSON with {'summary': '...', 'files': [{'path': '...', 'content': '...'}]} "
            "covering ONLY owned files (<= 300 lines/file)."
            if self._node_response_schema is not None
            else ""
        )
        instruction = (
            f"Implement node {node_snap.id}: {node_snap.title}. "
            f"Owned files: {', '.join(node_snap.owned_files) or 'none'}.{schema_hint}{defect_note}"
        )
        exec_res = await runtime.execute_leased_node(
            node_id=node_id,
            lease=lease,
            instruction=instruction,
            response_schema=self._node_response_schema,
        )

        latest_node = self._store.snapshot().nodes[node_id]
        eval_res = await self._verify_cb(latest_node, exec_res, judge)
        verdict: GateVerdict = evaluate_g2_node_verification(
            latest_node,
            author_agent_id=worker.agent_id,
            reviewer_charter=judge,
            sandbox_passed=eval_res.passed,
            verifier_defects=eval_res.defects,
        )
        if verdict.passed:
            await self._store.mutate(
                actor=judge.agent_id,
                mutation_type="transition_node",
                payload={"node_id": node_id, "status": "DONE", "rework_defects": []},
            )
            self._talents.evolve_profile(
                worker.role_id,
                nodes_passed_delta=1 if latest_node.retry_count == 0 else 0,
                nodes_total_delta=1,
            )
            return

        next_retry = latest_node.retry_count + 1
        if next_retry > self._max_retries:
            await self._store.mutate(
                actor=judge.agent_id,
                mutation_type="transition_node",
                payload={
                    "node_id": node_id,
                    "status": "BLOCKED_HUMAN",
                    "retry_count": next_retry,
                    "rework_defects": list(verdict.blocking_reasons),
                },
            )
        else:
            await self._store.mutate(
                actor=judge.agent_id,
                mutation_type="transition_node",
                payload={
                    "node_id": node_id,
                    "status": "NEEDS_REWORK",
                    "retry_count": next_retry,
                    "rework_defects": list(verdict.blocking_reasons),
                },
            )

    def _check_project_budgets(self, snap: MentalMap, *, elapsed_s: float) -> None:
        if self._project_wallclock_s is not None and elapsed_s > self._project_wallclock_s:
            raise BudgetExceeded(
                f"Project wall-clock budget exceeded "
                f"({elapsed_s:.1f}s > {self._project_wallclock_s:.1f}s)"
            )
        if self._project_token_limit is not None:
            used = sum(n.tokens_used for n in snap.nodes.values())
            if used >= self._project_token_limit:
                raise BudgetExceeded(
                    f"Project token budget exceeded ({used} >= {self._project_token_limit})"
                )


async def _default_pass_verifier(
    node: MapNode,
    result: AgentExecutionResult,
    judge: AgentCharter,
) -> JudgeEvaluation:
    _ = (node, judge)
    if not result.content_text.strip():
        return JudgeEvaluation(passed=False, defects=("Empty agent output",))
    return JudgeEvaluation(passed=True)
