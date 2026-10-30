"""Independent Deterministic Judge combining separation-of-duties, sandbox, and domain checks."""

from __future__ import annotations

import hashlib
import random
from typing import TYPE_CHECKING, Protocol

from orchestra.agents.charter import AgentCharter, assert_independent_reviewer
from orchestra.config.constants import MUTATION_CHECK_PROBABILITY
from orchestra.swarm.orchestrator import JudgeEvaluation
from orchestra.talents.diary_manager import EmpiricalProof
from orchestra.verify.sandbox import ExecutionSandbox
from orchestra.verify.verifiers import (
    run_sandbox_tool_verifiers,
    run_spot_mutation_check,
    verify_frozen_contracts_intact,
    verify_grounded_imports,
)

if TYPE_CHECKING:
    from pathlib import Path

    from orchestra.agents.agent_runtime import AgentExecutionResult
    from orchestra.state.contracts import ContractSignature
    from orchestra.state.mental_map import MapNode


class DomainVerifierHook(Protocol):
    """Optional domain-specific AST/static verifier hook (e.g., CryptoTradingDomainPack)."""

    def verify_workspace(self, workspace_dir: Path) -> list[str]:
        """Return list of domain rule violations (empty list if clean)."""
        ...


class IndependentJudge:
    """Renders deterministic PASS/REWORK verdicts without trusting author self-reports (INV-04)."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        domain_verifier: DomainVerifierHook | None = None,
        run_ruff: bool = True,
        run_mypy: bool = True,
        run_pytest: bool = True,
        mutation_probability: float = MUTATION_CHECK_PROBABILITY,
        seed: int = 1337,
    ) -> None:
        """Initialize IndependentJudge for `workspace_dir`."""
        self._workspace_dir = workspace_dir.resolve()
        self._sandbox = ExecutionSandbox(self._workspace_dir)
        self._domain_verifier = domain_verifier
        self._run_ruff = run_ruff
        self._run_mypy = run_mypy
        self._run_pytest = run_pytest
        self._mut_prob = mutation_probability
        self._rng = random.Random(seed)

    async def evaluate_node(
        self,
        node: MapNode,
        exec_result: AgentExecutionResult,
        judge_charter: AgentCharter,
        *,
        contracts: tuple[ContractSignature, ...] = (),
    ) -> tuple[JudgeEvaluation, EmpiricalProof | None]:
        """Run full verification suite as an independent judge and return verdict + proof."""
        assert_independent_reviewer(
            author_agent_id=exec_result.agent_id,
            reviewer_charter=judge_charter,
        )

        defects: list[str] = []
        defects.extend(verify_frozen_contracts_intact(contracts))

        # Check grounded imports on owned Python files
        for rel_file in node.owned_files:
            fpath = self._workspace_dir / rel_file
            if fpath.is_file() and fpath.suffix == ".py":
                try:
                    verify_grounded_imports(fpath, workspace_root=self._workspace_dir)
                except Exception as exc:
                    defects.append(str(exc))

        if self._domain_verifier is not None:
            defects.extend(self._domain_verifier.verify_workspace(self._workspace_dir))

        suite = await run_sandbox_tool_verifiers(
            self._sandbox,
            run_ruff=self._run_ruff,
            run_mypy=self._run_mypy,
            run_pytest=self._run_pytest,
        )
        defects.extend(suite.defects)

        if not defects and node.owned_files and self._rng.random() < self._mut_prob:
            py_owned = [f for f in node.owned_files if f.endswith(".py") and "test" not in f]
            if py_owned:
                killed, msg = await run_spot_mutation_check(self._workspace_dir, py_owned[0])
                if not killed:
                    defects.append(msg)

        if defects:
            return (JudgeEvaluation(passed=False, defects=tuple(defects)), None)

        hasher = hashlib.sha256()
        for rel_file in sorted(node.owned_files):
            fpath = self._workspace_dir / rel_file
            if fpath.is_file():
                hasher.update(fpath.read_bytes())
        proof = EmpiricalProof(
            command=" && ".join(suite.commands_run) or "ast+static+domain",
            exit_code=0,
            artifact_sha256=hasher.hexdigest() or "empty_workspace_verified",
            observed_summary=f"Node {node.id} passed all deterministic verifiers",
            verified_by_judge=True,
        )
        return (JudgeEvaluation(passed=True, defects=()), proof)
