"""Live end-to-end build pipeline wiring Architect blueprint, PatchEngine, Judge, and GitOps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import MAX_CHUNK_LINES
from orchestra.domains.crypto_trading import CryptoTradingDomainPack
from orchestra.providers.base import ChatMessage
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import AcceptanceCriterion, MapNode, MentalMap, NodeType
from orchestra.swarm.orchestrator import JudgeEvaluation
from orchestra.verify.git_ops import GitOps
from orchestra.verify.judge import IndependentJudge
from orchestra.verify.patch_engine import CreateFileOp, PatchEngine

if TYPE_CHECKING:
    from pathlib import Path

    from orchestra.agents.agent_runtime import AgentExecutionResult
    from orchestra.agents.charter import AgentCharter
    from orchestra.inference.llm_client import DualStreamLLMRuntime


class GeneratedFilePayload(BaseModel):
    """Single file artifact produced by an agent for its leased node."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str


class NodeArtifactPayload(BaseModel):
    """Structured JSON output schema requested from agents during live workspace builds."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="Implemented node")
    files: list[GeneratedFilePayload] = Field(default_factory=list)


class BlueprintNodeItem(BaseModel):
    """Single DAG node proposed by the Architect during live S2/S4 decomposition."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=2)
    title: str = Field(min_length=3)
    node_type: NodeType = "code"
    owner_role: str = "developer"
    dependencies: list[str] = Field(default_factory=list)
    owned_files: list[str] = Field(min_length=1)
    contract_definition: str = ""


class ArchitectBlueprintPayload(BaseModel):
    """Structured DAG blueprint returned by the Meta-Architect for a raw user specification."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    nodes: list[BlueprintNodeItem] = Field(min_length=1, max_length=20)


async def build_initial_mental_map(
    *,
    project_id: str,
    spec_text: str,
    detected_domain: str,
    mock: bool,
    llm_runtime: DualStreamLLMRuntime,
) -> MentalMap:
    """Construct validated `MentalMap` via live Architect LLM or deterministic Mock blueprint."""
    if mock:
        if detected_domain == "crypto_trading":
            return CryptoTradingDomainPack().build_reference_map(project_id=project_id)
        return _fallback_two_node_map(project_id=project_id, spec_text=spec_text)

    prompt = (
        "ROLE: META-ARCHITECT. Decompose the user specification into an acyclic Python DAG "
        "(2..12 nodes). Every node MUST own distinct relative POSIX file paths (e.g. "
        "'src/module.py' or 'tests/test_module.py'), have no cyclic dependencies, and include "
        "at least one dedicated test node owned by 'qa_adversary'. Return strict JSON matching "
        "the schema.\n\nUSER SPECIFICATION:\n" + spec_text
    )
    res = await llm_runtime.complete(
        [ChatMessage(role="user", content=prompt)],
        role="lead_architect",
        agent_id="lead_architect_01",
        response_schema=ArchitectBlueprintPayload,
    )
    if res.structured is None:
        return _fallback_two_node_map(project_id=project_id, spec_text=spec_text)

    bp = res.structured
    edges = tuple(f"edge_case_{i}" for i in range(1, 11))
    ac = (AcceptanceCriterion(id="AC1", description="IndependentJudge exit_code == 0"),)
    nodes: dict[str, MapNode] = {}
    contracts: dict[str, ContractSignature] = {}
    valid_ids = {item.id for item in bp.nodes}

    for item in bp.nodes:
        deps = tuple(d for d in item.dependencies if d in valid_ids and d != item.id)
        cid_tuple: tuple[str, ...] = ()
        if item.contract_definition.strip():
            cid = f"C_{item.id}"
            contracts[cid] = ContractSignature.create(
                contract_id=cid,
                owner_node_id=item.id,
                definition=item.contract_definition.strip(),
                frozen=True,
            )
            cid_tuple = (cid,)
        nodes[item.id] = MapNode(
            id=item.id,
            title=item.title,
            type=item.node_type,
            owner_role=item.owner_role,
            status="READY" if not deps else "PENDING_DEPS",
            dependencies=deps,
            contracts_provided=cid_tuple,
            owned_files=tuple(item.owned_files),
            acceptance_criteria=ac,
            edge_cases=edges,
        )

    mmap = MentalMap(
        project_id=project_id,
        project_name=bp.project_name or project_id,
        domain=detected_domain,
        vision=spec_text[:500],
        nodes=nodes,
        contracts=contracts,
    )
    mmap.verify_integrity()
    return mmap


class WorkspaceBuildVerifier:
    """Applies agent file artifacts to `workspace/`, runs `IndependentJudge`, and commits to Git."""

    def __init__(
        self,
        *,
        workspace_dir: Path,
        detected_domain: str,
    ) -> None:
        """Bind `PatchEngine`, `IndependentJudge`, and `GitOps` to `workspace_dir`."""
        self._ws = workspace_dir
        self._patch_engine = PatchEngine(workspace_dir)
        self._git = GitOps(workspace_dir)
        domain_pack = CryptoTradingDomainPack() if detected_domain == "crypto_trading" else None
        self._judge = IndependentJudge(
            workspace_dir,
            domain_verifier=domain_pack,
            run_mypy=False,
            mutation_probability=0.0,
        )

    async def verify_and_commit(
        self,
        node: MapNode,
        exec_res: AgentExecutionResult,
        judge_charter: AgentCharter,
    ) -> JudgeEvaluation:
        """Write owned files atomically, run deterministic Judge, and commit on green verdict."""
        raw_files = exec_res.proposed_payload.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            return JudgeEvaluation(
                passed=False,
                defects=("Agent returned zero files in structured payload",),
            )

        ops: list[CreateFileOp] = []
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path", "")).strip()
            content = str(entry.get("content", ""))
            line_count = len(content.splitlines())
            if line_count > MAX_CHUNK_LINES:
                return JudgeEvaluation(
                    passed=False,
                    defects=(
                        f"File '{rel}' has {line_count} lines > MAX_CHUNK_LINES={MAX_CHUNK_LINES}",
                    ),
                )
            # Allow overwrite on rework retry within owned_files
            target = self._ws / rel
            if target.exists() and rel in set(node.owned_files):
                target.unlink()
            ops.append(CreateFileOp(path=rel, content=content))

        try:
            self._patch_engine.apply_transaction(
                tuple(ops),
                allowed_owned_files=set(node.owned_files),
            )
        except Exception as exc:
            return JudgeEvaluation(passed=False, defects=(f"PatchEngine rejected: {exc}",))

        verdict, _ = await self._judge.evaluate_node(node, exec_res, judge_charter)
        if not verdict.passed:
            return JudgeEvaluation(passed=False, defects=verdict.defects)

        await self._git.commit_all(f"feat({node.id.lower()}): {node.title}")
        return JudgeEvaluation(passed=True)


def _fallback_two_node_map(*, project_id: str, spec_text: str) -> MentalMap:
    c_core = ContractSignature.create(
        contract_id="C_CORE",
        owner_node_id="N1",
        consumer_node_ids=("N2",),
        definition="class CoreService:\n    def execute(self) -> bool: ...",
        frozen=True,
    )
    edges = tuple(f"edge_{i}" for i in range(10))
    ac = (AcceptanceCriterion(id="AC1", description="Verified by Independent Judge"),)
    return MentalMap(
        project_id=project_id,
        project_name=project_id,
        vision=spec_text[:500],
        nodes={
            "N1": MapNode(
                id="N1",
                title="Core Module",
                status="READY",
                contracts_provided=("C_CORE",),
                owned_files=("src/core.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
            "N2": MapNode(
                id="N2",
                title="Verification & Integration",
                type="test",
                owner_role="qa_adversary",
                status="PENDING_DEPS",
                dependencies=("N1",),
                contracts_consumed=("C_CORE",),
                owned_files=("tests/test_core.py",),
                acceptance_criteria=ac,
                edge_cases=edges,
            ),
        },
        contracts={"C_CORE": c_core},
    )
