"""E2E smoke test T-01: build Todo API project offline on MockProvider with real Sandbox & Git."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestra.agents.agent_runtime import AgentExecutionResult
from orchestra.agents.roles_catalog import instantiate_charter
from orchestra.config.settings import load_settings
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime
from orchestra.providers.mock import MockProvider
from orchestra.research.pre_study import PreStudyRunner
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import AcceptanceCriterion, MapNode, MentalMap
from orchestra.state.state_store import StateStore
from orchestra.swarm.orchestrator import SwarmOrchestrator
from orchestra.talents.talents_store import TalentsStore
from orchestra.verify.git_ops import GitOps
from orchestra.verify.judge import IndependentJudge
from orchestra.verify.patch_engine import CreateFileOp, PatchEngine

if TYPE_CHECKING:
    from pathlib import Path


async def test_factory_builds_todo_api_e2e_offline(tmp_path: Path) -> None:
    """Verify T-01 (M-01): full S1..S14 build on MockProvider + real PatchEngine + Judge + Git."""
    rt_dir = tmp_path / "runtime"
    ws_dir = tmp_path / "todo_workspace"
    ws_dir.mkdir(parents=True)

    git = GitOps(ws_dir)
    await git.init_workspace_repo(working_branch="orchestra/todo-api")

    settings = load_settings(overrides={"runtime_dir": str(rt_dir), "llm": {"provider": "mock"}})
    pre = PreStudyRunner(docs_cache_dir=settings.paths.docs_cache_dir).run(
        project_id="todo_api",
        specification_text="Build deterministic Todo service with typed repository",
    )
    assert pre.detected_domain == "general"

    contract = ContractSignature.create(
        contract_id="C_TODO_STORE",
        owner_node_id="N_MODEL",
        consumer_node_ids=("N_TESTS",),
        definition="class TodoStore:\n    def add(self, title: str) -> int: ...",
        frozen=True,
    )
    edges = tuple(f"edge_{i}" for i in range(10))
    ac = (AcceptanceCriterion(id="AC1", description="Judge exit code 0"),)

    init_map = MentalMap(
        project_id="todo_api",
        project_name="Todo API",
        vision="Offline Todo service",
        nodes={
            "N_MODEL": MapNode(
                id="N_MODEL",
                title="Todo Store Module",
                status="READY",
                contracts_provided=("C_TODO_STORE",),
                owned_files=("todo_store.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
            "N_TESTS": MapNode(
                id="N_TESTS",
                title="Todo Store Tests",
                type="test",
                owner_role="qa_adversary",
                status="PENDING_DEPS",
                dependencies=("N_MODEL",),
                contracts_consumed=("C_TODO_STORE",),
                owned_files=("test_todo_store.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
        },
        contracts={"C_TODO_STORE": contract},
    )

    patch_engine = PatchEngine(ws_dir)
    patch_engine.apply_transaction(
        [
            CreateFileOp(
                path="todo_store.py",
                content=(
                    '"""Deterministic in-memory TodoStore."""\n\n'
                    "from __future__ import annotations\n\n\n"
                    "class TodoStore:\n"
                    '    """Simple typed Todo repository."""\n\n'
                    "    def __init__(self) -> None:\n"
                    '        """Initialize empty items map."""\n'
                    "        self._items: dict[int, str] = {}\n\n"
                    "    def add(self, title: str) -> int:\n"
                    '        """Add non-empty todo item and return 1-based ID."""\n'
                    "        cleaned = title.strip()\n"
                    "        if not cleaned:\n"
                    '            raise ValueError("empty title")\n'
                    "        item_id = len(self._items) + 1\n"
                    "        self._items[item_id] = cleaned\n"
                    "        return item_id\n"
                ),
            ),
            CreateFileOp(
                path="test_todo_store.py",
                content=(
                    '"""Unit tests for TodoStore."""\n\n'
                    "from __future__ import annotations\n\n"
                    "import pytest\n\n"
                    "from todo_store import TodoStore\n\n\n"
                    "def test_add_and_reject_empty() -> None:\n"
                    "    store = TodoStore()\n"
                    '    assert store.add("Write ADR") == 1\n'
                    "    with pytest.raises(ValueError):\n"
                    '        store.add("   ")\n'
                ),
            ),
        ],
        allowed_owned_files={"todo_store.py", "test_todo_store.py"},
    )

    store = StateStore(settings.paths.projects_dir / "todo_api", initial_map=init_map)
    await store.start()
    llm = DualStreamLLMRuntime(
        provider=MockProvider(),
        settings=settings,
        key_pool=KeyPoolManager([]),
    )
    judge = IndependentJudge(ws_dir, mutation_probability=0.0)
    qa_charter = instantiate_charter(
        "qa_adversary",
        instance_index=1,
        overrides={"can_approve_patches": True},
    )

    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=TalentsStore(settings.paths.talents_dir),
    )
    summary = await orch.run_build_loop()
    assert set(summary.completed_nodes) == {"N_MODEL", "N_TESTS"}

    verdict, proof = await judge.evaluate_node(
        summary.final_map.nodes["N_MODEL"],
        AgentExecutionResult(
            agent_id="core_dev_01",
            role_id="developer",
            node_id="N_MODEL",
            content_text="Implemented TodoStore",
            tokens_used=120,
            proposed_payload={"files": ["todo_store.py"]},
        ),
        qa_charter,
        contracts=(contract,),
    )
    assert verdict.passed is True, verdict.defects
    assert proof is not None and proof.exit_code == 0

    sha = await git.commit_all("feat(todo): TodoStore and unit tests verified")
    assert len(sha) >= 7
    await store.stop()
