"""Formal pipeline gates G0..G4 enforcing architectural and verification invariants (§16.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.agents.charter import AgentCharter, assert_independent_reviewer
from orchestra.agents.roles_catalog import validate_team_roster
from orchestra.config.constants import (
    COVERAGE_CORE_MIN,
    COVERAGE_TOTAL_MIN,
    MIN_EDGE_CASES_PER_NODE,
)
from orchestra.state.mental_map import MapNode, MentalMap

GateId = Literal["G0", "G1", "G2", "G3", "G4"]


class GateVerdict(BaseModel):
    """Deterministic pass/block outcome of a pipeline quality gate (§16.2, SW-16..19)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: GateId
    passed: bool
    blocking_reasons: tuple[str, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def evaluate_g0_team_and_vision(
    mental_map: MentalMap,
    charters: tuple[AgentCharter, ...],
    *,
    approved_by_human: bool,
    require_approval: bool = True,
) -> GateVerdict:
    """Gate G0: Validate vision clarity, team roster rules, and optional human sign-off."""
    reasons: list[str] = []
    if not mental_map.vision.strip():
        reasons.append("Project vision in MentalMap is empty")
    try:
        validate_team_roster(charters)
    except Exception as exc:
        reasons.append(str(exc))
    if require_approval and not approved_by_human:
        reasons.append("Awaiting human operator approval of team roster (G0)")
    return GateVerdict(
        gate_id="G0",
        passed=not reasons,
        blocking_reasons=tuple(reasons),
        metrics={"team_size": len(charters)},
    )


def evaluate_g1_architecture_and_contracts(
    mental_map: MentalMap,
    *,
    min_edge_cases: int = MIN_EDGE_CASES_PER_NODE,
) -> GateVerdict:
    """Gate G1: Require valid DAG, frozen contracts, and edge-case matrix on code nodes."""
    reasons: list[str] = []
    if not mental_map.nodes:
        reasons.append("MentalMap contains zero nodes")
    try:
        mental_map.verify_integrity()
    except Exception as exc:
        reasons.append(f"MentalMap integrity failure: {exc}")

    unfrozen = [cid for cid, c in mental_map.contracts.items() if not c.frozen]
    if unfrozen:
        reasons.append(f"Unfrozen contracts block G1: {sorted(unfrozen)}")

    for nid, node in sorted(mental_map.nodes.items()):
        if node.type == "code":
            if not node.acceptance_criteria:
                reasons.append(f"Code node '{nid}' has zero acceptance criteria")
            if len(node.edge_cases) < min_edge_cases:
                reasons.append(
                    f"Code node '{nid}' has {len(node.edge_cases)}/{min_edge_cases} edge cases"
                )

    return GateVerdict(
        gate_id="G1",
        passed=not reasons,
        blocking_reasons=tuple(reasons),
        metrics={"nodes": len(mental_map.nodes), "contracts": len(mental_map.contracts)},
    )


def evaluate_g2_node_verification(
    node: MapNode,
    *,
    author_agent_id: str,
    reviewer_charter: AgentCharter,
    sandbox_passed: bool,
    verifier_defects: tuple[str, ...] = (),
) -> GateVerdict:
    """Gate G2: Enforce independent judge authority (INV-04) and zero verifier defects."""
    reasons: list[str] = []
    try:
        assert_independent_reviewer(
            author_agent_id=author_agent_id,
            reviewer_charter=reviewer_charter,
        )
    except Exception as exc:
        reasons.append(str(exc))

    if not sandbox_passed:
        reasons.append(f"Sandbox verification failed for node '{node.id}'")
    reasons.extend(verifier_defects)

    return GateVerdict(
        gate_id="G2",
        passed=not reasons,
        blocking_reasons=tuple(reasons),
        metrics={"node_id": node.id, "reviewer": reviewer_charter.agent_id},
    )


def evaluate_g3_integration_and_coverage(
    mental_map: MentalMap,
    *,
    integration_tests_passed: bool,
    core_coverage_pct: float,
    total_coverage_pct: float,
    min_core_pct: float = COVERAGE_CORE_MIN,
    min_total_pct: float = COVERAGE_TOTAL_MIN,
) -> GateVerdict:
    """Gate G3: Require all build/test nodes DONE, green integration tests, and coverage floors."""
    reasons: list[str] = []
    incomplete = [
        nid
        for nid, n in sorted(mental_map.nodes.items())
        if n.type in ("code", "test", "integration") and n.status != "DONE"
    ]
    if incomplete:
        reasons.append(f"Incomplete build/integration nodes: {incomplete}")
    if not integration_tests_passed:
        reasons.append("End-to-end / integration test suite failed")
    if core_coverage_pct < min_core_pct:
        reasons.append(f"Core coverage {core_coverage_pct:.1f}% < {min_core_pct:.1f}%")
    if total_coverage_pct < min_total_pct:
        reasons.append(f"Total coverage {total_coverage_pct:.1f}% < {min_total_pct:.1f}%")

    return GateVerdict(
        gate_id="G3",
        passed=not reasons,
        blocking_reasons=tuple(reasons),
        metrics={"core_coverage_pct": core_coverage_pct, "total_coverage_pct": total_coverage_pct},
    )


def evaluate_g4_final_release(
    mental_map: MentalMap,
    *,
    secrets_clean: bool,
    docs_complete: bool,
    domain_gate_passed: bool = True,
) -> GateVerdict:
    """Gate G4: Final release gate checking all nodes DONE, zero open Qs, and clean secrets."""
    reasons: list[str] = []
    unfinished = [
        nid for nid, n in sorted(mental_map.nodes.items()) if n.status not in ("DONE", "CANCELLED")
    ]
    if unfinished:
        reasons.append(f"Nodes not in DONE/CANCELLED state: {unfinished}")
    open_qs = [qid for qid, q in mental_map.open_questions.items() if q.status == "OPEN"]
    if open_qs:
        reasons.append(f"Unanswered open questions remain: {sorted(open_qs)}")
    if not secrets_clean:
        reasons.append("Secret leak detector found suspicious credentials")
    if not docs_complete:
        reasons.append("Mandatory release documentation (README/RUNBOOK/CHANGELOG) incomplete")
    if not domain_gate_passed:
        reasons.append("Domain pack acceptance gate failed")

    return GateVerdict(
        gate_id="G4",
        passed=not reasons,
        blocking_reasons=tuple(reasons),
        metrics={"total_nodes": len(mental_map.nodes)},
    )
