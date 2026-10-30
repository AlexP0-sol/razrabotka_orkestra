"""Unit tests for Phase P4: Agent Charters, Roles Catalog, TalentsStore, Gates, and Swarm."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestra.agents.charter import IndependentReviewViolationError, assert_independent_reviewer
from orchestra.agents.roles_catalog import (
    CRYPTO_ROLE_TEMPLATES,
    PERMANENT_ROLE_TEMPLATES,
    instantiate_charter,
)
from orchestra.config.settings import load_settings
from orchestra.providers.mock import MockProvider
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import AcceptanceCriterion, MapNode, MentalMap
from orchestra.state.state_store import StateStore
from orchestra.swarm.gates import (
    evaluate_g0_team_and_vision,
    evaluate_g1_architecture_and_contracts,
    evaluate_g3_integration_and_coverage,
    evaluate_g4_final_release,
)
from orchestra.swarm.orchestrator import JudgeEvaluation, SwarmOrchestrator
from orchestra.swarm.org_designer import OrgDesigner
from orchestra.swarm.scheduler import CriticalPathScheduler
from orchestra.talents.talents_store import TalentsStore

if TYPE_CHECKING:
    from pathlib import Path

    from orchestra.agents.agent_runtime import AgentExecutionResult
    from orchestra.agents.charter import AgentCharter


def _build_dag_map(*, domain: str = "general") -> MentalMap:
    c1 = ContractSignature.create(
        contract_id="C1",
        owner_node_id="N1",
        consumer_node_ids=("N2", "N3"),
        definition="class Store(Protocol): ...",
        frozen=True,
    )
    edges = tuple(f"edge_case_{i}" for i in range(10))
    crit = (AcceptanceCriterion(id="AC1", description="Unit tests pass"),)
    return MentalMap(
        project_id="proj_swarm",
        project_name="Swarm Demo",
        domain=domain,
        vision="Build reliable multi-agent system",
        nodes={
            "N1": MapNode(
                id="N1",
                title="Base Storage",
                status="READY",
                contracts_provided=("C1",),
                owned_files=("src/storage.py",),
                acceptance_criteria=crit,
                edge_cases=edges,
            ),
            "N2": MapNode(
                id="N2",
                title="Service A",
                status="PENDING_DEPS",
                dependencies=("N1",),
                contracts_consumed=("C1",),
                owned_files=("src/service_a.py",),
                acceptance_criteria=crit,
                edge_cases=edges,
            ),
            "N3": MapNode(
                id="N3",
                title="Service B",
                status="PENDING_DEPS",
                dependencies=("N1",),
                contracts_consumed=("C1",),
                owned_files=("src/service_b.py",),
                acceptance_criteria=crit,
                edge_cases=edges,
            ),
            "N4": MapNode(
                id="N4",
                title="Aggregator",
                status="PENDING_DEPS",
                dependencies=("N2", "N3"),
                owned_files=("src/agg.py",),
                acceptance_criteria=crit,
                edge_cases=edges,
            ),
        },
        contracts={"C1": c1},
    ).with_updated_checksum()


def test_charter_separation_of_duties_and_org_designer(tmp_path: Path) -> None:
    """Verify INV-04 self-review prohibition, 7 crypto roles, TalentsStore, and Gates G0..G4."""
    assert len(PERMANENT_ROLE_TEMPLATES) == 4
    assert len(CRYPTO_ROLE_TEMPLATES) == 7

    dev = instantiate_charter("core_developer", instance_index=1)
    judge = instantiate_charter("independent_judge", instance_index=1)

    with pytest.raises(IndependentReviewViolationError):
        assert_independent_reviewer(author_agent_id=dev.agent_id, reviewer_charter=dev)
    with pytest.raises(IndependentReviewViolationError):
        assert_independent_reviewer(author_agent_id=judge.agent_id, reviewer_charter=judge)
    assert_independent_reviewer(author_agent_id=dev.agent_id, reviewer_charter=judge)

    designer = OrgDesigner(max_concurrent_agents=4)
    gen_map = _build_dag_map(domain="general")
    proposal = designer.design_team(gen_map)
    assert "Состав роя" in proposal.format_sitrep_ru()
    assert evaluate_g0_team_and_vision(gen_map, proposal.charters, approved_by_human=True).passed
    assert evaluate_g1_architecture_and_contracts(gen_map).passed

    crypto_proposal = designer.design_team(_build_dag_map(domain="crypto_trading"))
    crypto_roles = {c.role_id for c in crypto_proposal.charters}
    assert {"quant_strategist", "risk_guardian", "execution_oms_engineer"} <= crypto_roles

    # TalentsStore persistence & evolution
    tstore = TalentsStore(tmp_path / "talents")
    prof1 = tstore.hire_or_create(dev)
    assert prof1.version == 1
    prof2 = tstore.evolve_profile(
        dev.role_id,
        new_heuristics=("Always validate input bounds",),
        nodes_passed_delta=3,
        nodes_total_delta=4,
        project_completed=True,
    )
    assert prof2.version == 2
    assert prof2.first_pass_rate == pytest.approx(0.75)
    assert (tstore.role_dir(dev.role_id) / "history" / "profile.v1.json").is_file()


def test_critical_path_scheduler_depths() -> None:
    """Verify CriticalPathScheduler computes bottom-level DAG depths accurately."""
    sched = CriticalPathScheduler(max_concurrent_agents=3)
    dag = _build_dag_map()
    depths = sched.compute_critical_depths(dag)
    assert depths["N1"] == 3
    assert depths["N2"] == 2
    assert depths["N3"] == 2
    assert depths["N4"] == 1


async def test_swarm_orchestrator_parallel_build_rework_and_escalation(tmp_path: Path) -> None:
    """Verify SwarmOrchestrator executes DAG in parallel, retries rework once, and completes."""
    from orchestra.inference.llm_client import DualStreamLLMRuntime

    settings = load_settings(
        env_file=None,
        overrides={
            "runtime_dir": str(tmp_path / "rt"),
            "llm": {"provider": "mock", "cache_enabled": False},
        },
    )
    store = StateStore(tmp_path / "rt" / "projects" / "p1", initial_map=_build_dag_map())
    await store.start()
    llm = DualStreamLLMRuntime(provider=MockProvider(), settings=settings)
    talents = TalentsStore(settings.paths.talents_dir)

    attempts_seen: dict[str, int] = {}

    async def _flaky_verifier(
        node: MapNode,
        result: AgentExecutionResult,
        judge: AgentCharter,
    ) -> JudgeEvaluation:
        _ = (result, judge)
        count = attempts_seen.get(node.id, 0) + 1
        attempts_seen[node.id] = count
        # Force N2 through 1 rework iteration before passing!
        if node.id == "N2" and count == 1:
            return JudgeEvaluation(passed=False, defects=("Missing edge check on empty input",))
        return JudgeEvaluation(passed=True)

    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=talents,
        verify_callback=_flaky_verifier,
        max_concurrent_agents=4,
        max_retries_per_node=2,
    )
    summary = await orch.run_build_loop()
    assert set(summary.completed_nodes) == {"N1", "N2", "N3", "N4"}
    assert summary.blocked_nodes == ()
    assert summary.final_map.stage == "S8_INTEGRATION"
    assert attempts_seen["N2"] == 2

    g3 = evaluate_g3_integration_and_coverage(
        summary.final_map,
        integration_tests_passed=True,
        core_coverage_pct=91.0,
        total_coverage_pct=82.0,
    )
    assert g3.passed is True
    g4 = evaluate_g4_final_release(
        summary.final_map,
        secrets_clean=True,
        docs_complete=True,
    )
    assert g4.passed is True
    await store.stop()
