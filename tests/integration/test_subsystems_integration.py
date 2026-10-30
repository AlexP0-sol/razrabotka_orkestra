"""Integration tests: LLM+KeyPool+Metrics, lease cancellation, non-blocking escalation, skills."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from orchestra.comms.escalation import EscalationManager, EscalationRequest
from orchestra.config.settings import load_settings
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime, LLMResult
from orchestra.observability.metrics import MetricsCollector
from orchestra.providers.base import ChatMessage
from orchestra.providers.mock import MockProvider, MockScenario, MockStep
from orchestra.skills.executor import SkillExecutor
from orchestra.skills.mutation_gate import SkillMutationGate, SkillTestVector
from orchestra.skills.registry import SkillRegistry
from orchestra.skills.synthesizer import SkillSynthesizer
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import AcceptanceCriterion, MapNode, MentalMap
from orchestra.state.state_store import StateStore
from orchestra.swarm.orchestrator import SwarmOrchestrator
from orchestra.talents.diary_manager import DiaryEntry, DiaryManager, EmpiricalProof
from orchestra.talents.post_mortem import PostMortemAnalyzer
from orchestra.talents.talents_store import TalentsStore

if TYPE_CHECKING:
    from pathlib import Path


async def test_t04_and_dod11_dual_stream_separation_and_exact_token_accounting(
    tmp_path: Path,
) -> None:
    """Verify T-04 & DoD-11: reasoning never leaks into content; MetricsCollector matches usage."""
    provider = MockProvider(
        MockScenario(
            steps=[
                MockStep(
                    reasoning_chunks=("1 BOUNDARY ANALYSIS: PRIVATE_REASONING_CHAIN_XYZ.",),
                    content_chunks=('{"result": "CLEAN_ARTIFACT"}',),
                )
            ]
        )
    )
    settings = load_settings(
        overrides={"runtime_dir": str(tmp_path / "rt"), "llm": {"provider": "mock"}}
    )
    pool = KeyPoolManager(["nvapi-key-a-0001", "nvapi-key-b-0002"])
    llm = DualStreamLLMRuntime(provider=provider, settings=settings, key_pool=pool)
    metrics = MetricsCollector()

    res: LLMResult[Any] = await llm.complete(messages=[ChatMessage(role="user", content="Build")])
    metrics.record_llm_call(
        model=settings.llm.default_model,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        reasoning_tokens=res.reasoning_tokens,
    )

    assert "PRIVATE_REASONING_CHAIN_XYZ" in res.reasoning_text
    assert "PRIVATE_REASONING_CHAIN_XYZ" not in res.content_text
    snap = metrics.snapshot()
    assert snap.prompt_tokens_total == res.prompt_tokens
    assert snap.completion_tokens_total == res.completion_tokens
    assert snap.reasoning_tokens_total == res.reasoning_tokens


async def test_t08_cancelled_agent_task_releases_key_pool_lease() -> None:
    """Verify T-08: cancelling an async task mid-lease cleanly releases the API key slot."""
    pool = KeyPoolManager(["nvapi-" + "only-single-key-0001"], max_concurrent_per_key=1)
    entered = asyncio.Event()

    async def _worker() -> None:
        lease = await pool.acquire(agent_id="dev_01")
        try:
            entered.set()
            await asyncio.sleep(30.0)
        finally:
            await pool.release(lease)

    task = asyncio.create_task(_worker())
    await entered.wait()
    assert pool.snapshot_public()[0].active_leases == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.snapshot_public()[0].active_leases == 0


async def test_t09_non_blocking_escalation_allows_sibling_branch_progress(tmp_path: Path) -> None:
    """Verify T-09: while N_BLOCKED awaits human input, independent N_FAST completes to DONE."""
    edges = tuple(f"e{i}" for i in range(10))
    ac = (AcceptanceCriterion(id="AC1", description="Pass Judge"),)
    c1 = ContractSignature.create(
        contract_id="C_FAST",
        owner_node_id="N_FAST",
        definition="def ping() -> bool: ...",
        frozen=True,
    )
    mmap = MentalMap(
        project_id="p_nonblock",
        project_name="NonBlocking",
        nodes={
            "N_BLOCKED": MapNode(
                id="N_BLOCKED",
                title="Needs Human",
                status="READY",
                owned_files=("src/b.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
            "N_FAST": MapNode(
                id="N_FAST",
                title="Independent Branch",
                status="READY",
                contracts_provided=("C_FAST",),
                owned_files=("src/f.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
        },
        contracts={"C_FAST": c1},
    )
    store = StateStore(tmp_path / "proj", initial_map=mmap)
    await store.start()
    esc = EscalationManager(state_store=store)
    await esc.escalate(
        EscalationRequest(
            author_agent="lead_architect_01",
            node_id="N_BLOCKED",
            is_business_decision=True,
            question_ru="Выбрать тариф?",
            options_ru=("Free", "Pro"),
        )
    )
    assert store.snapshot().nodes["N_BLOCKED"].status == "BLOCKED_HUMAN"

    settings = load_settings(
        overrides={"runtime_dir": str(tmp_path / "rt"), "llm": {"provider": "mock"}}
    )
    llm = DualStreamLLMRuntime(
        provider=MockProvider(),
        settings=settings,
        key_pool=KeyPoolManager([]),
    )
    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=TalentsStore(tmp_path / "talents"),
    )
    summary = await orch.run_build_loop()
    assert "N_FAST" in summary.completed_nodes
    assert summary.final_map.nodes["N_FAST"].status == "DONE"
    assert summary.final_map.nodes["N_BLOCKED"].status == "BLOCKED_HUMAN"
    await store.stop()


async def test_t10_and_t11_skill_synthesis_and_postmortem_diary_pipeline(tmp_path: Path) -> None:
    """Verify T-10 & T-11: SkillSynthesizer + MutationGate and PostMortem -> DiaryManager."""
    reg = SkillRegistry(tmp_path / "skills", load_builtins=False)
    gate = SkillMutationGate(reg, SkillExecutor())
    synth = SkillSynthesizer(gate=gate)

    spec = await synth.synthesize_and_register(
        skill_id="double_int",
        name="Double Int",
        description="Doubles integer x.",
        tags=("math",),
        run_function_source=(
            "def run(payload: dict[str, Any]) -> dict[str, Any]:\n"
            "    return {'doubled': int(payload['x']) * 2}\n"
        ),
        input_schema={"type": "object", "required": ["x"]},
        output_schema={"type": "object", "required": ["doubled"]},
        test_vectors=[
            SkillTestVector(
                input_payload={"x": 21},
                predicate=lambda out: out.get("doubled") == 42,
            )
        ],
    )
    assert spec.version == "1.0.0"

    tstore = TalentsStore(tmp_path / "talents")
    diary_mgr = DiaryManager(tmp_path / "talents")
    proof = EmpiricalProof(
        command="pytest -q",
        exit_code=0,
        artifact_sha256="a" * 64,
        observed_summary="1 passed",
    )
    entry = DiaryEntry(
        role_id="developer",
        project_id="proj_pm",
        status="VERIFIED",
        observation="HTTP call lacked explicit timeout",
        lesson="ALWAYS pass timeout_s to HTTP client calls",
        tags=("http", "timeout"),
        proof=proof,
    )
    diary_mgr.append(entry)
    # Appending duplicate lesson deduplicates cleanly
    diary_mgr.append(entry)
    assert len(diary_mgr.read_entries("developer", status="VERIFIED")) == 1

    pm = PostMortemAnalyzer(talents_store=tstore, diary_manager=diary_mgr)
    pm_map = MentalMap(
        project_id="proj_pm",
        project_name="PM",
        nodes={
            "N1": MapNode(
                id="N1",
                title="Retried Node",
                status="DONE",
                retry_count=1,
                rework_defects=("MISSING_TIMEOUT",),
            )
        },
    )
    report = pm.run_post_mortem(pm_map, participating_roles={"developer"})
    assert "developer" in report.roles_evolved


async def test_live_pipeline_architect_blueprint_patch_judge_and_git_commit(
    tmp_path: Path,
) -> None:
    """Verify live CLI pipeline: LLM blueprint -> structured files -> PatchEngine -> Judge."""
    import json

    from orchestra.swarm.live_pipeline import (
        NodeArtifactPayload,
        WorkspaceBuildVerifier,
        build_initial_mental_map,
    )
    from orchestra.verify.git_ops import GitOps

    blueprint_json = json.dumps(
        {
            "project_name": "AdderService",
            "nodes": [
                {
                    "id": "N_ADD",
                    "title": "Adder Module",
                    "node_type": "code",
                    "owner_role": "developer",
                    "dependencies": [],
                    "owned_files": ["adder.py"],
                    "contract_definition": "def add_ints(a: int, b: int) -> int: ...",
                },
                {
                    "id": "N_TEST",
                    "title": "Adder Unit Tests",
                    "node_type": "test",
                    "owner_role": "qa_adversary",
                    "dependencies": ["N_ADD"],
                    "owned_files": ["test_adder.py"],
                    "contract_definition": "",
                },
            ],
        }
    )
    node_add_json = json.dumps(
        {
            "summary": "Implemented add_ints",
            "files": [
                {
                    "path": "adder.py",
                    "content": (
                        '"""Adder module."""\n\n'
                        "from __future__ import annotations\n\n\n"
                        "def add_ints(a: int, b: int) -> int:\n"
                        '    """Return sum of two integers."""\n'
                        "    return a + b\n"
                    ),
                }
            ],
        }
    )
    node_test_json = json.dumps(
        {
            "summary": "Implemented tests",
            "files": [
                {
                    "path": "test_adder.py",
                    "content": (
                        '"""Tests for adder."""\n\n'
                        "from __future__ import annotations\n\n"
                        "from adder import add_ints\n\n\n"
                        "def test_add_ints() -> None:\n"
                        "    assert add_ints(2, 3) == 5\n"
                    ),
                }
            ],
        }
    )
    provider = MockProvider(
        MockScenario(
            steps=[
                MockStep(content_chunks=(blueprint_json,), max_uses=1),
                MockStep(content_chunks=(node_add_json,), max_uses=1),
                MockStep(content_chunks=(node_test_json,), max_uses=1),
            ]
        )
    )
    settings = load_settings(
        overrides={"runtime_dir": str(tmp_path / "rt"), "llm": {"provider": "mock"}}
    )
    llm = DualStreamLLMRuntime(
        provider=provider,
        settings=settings,
        key_pool=KeyPoolManager(["nvapi-" + "live-sim-key-0001"]),
    )
    mmap = await build_initial_mental_map(
        project_id="adder_live",
        spec_text="Build typed integer adder with unit test",
        detected_domain="general",
        mock=False,
        llm_runtime=llm,
    )
    assert set(mmap.nodes.keys()) == {"N_ADD", "N_TEST"}

    ws_dir = tmp_path / "ws_live"
    ws_dir.mkdir()
    await GitOps(ws_dir).init_workspace_repo(working_branch="orchestra/adder_live")

    store = StateStore(tmp_path / "proj_live", initial_map=mmap)
    await store.start()
    verifier = WorkspaceBuildVerifier(workspace_dir=ws_dir, detected_domain="general")
    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=TalentsStore(tmp_path / "talents_live"),
        verify_callback=verifier.verify_and_commit,
        node_response_schema=NodeArtifactPayload,
        max_concurrent_agents=2,
    )
    summary = await orch.run_build_loop()
    assert set(summary.completed_nodes) == {"N_ADD", "N_TEST"}
    assert (ws_dir / "adder.py").is_file()
    assert (ws_dir / "test_adder.py").is_file()
    await store.stop()
