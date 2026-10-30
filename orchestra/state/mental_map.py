"""Mental Map SSOT data model, node FSM, DAG validation, and projections (§17)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import LEASE_TTL_S, NODE_TOKEN_LIMIT
from orchestra.errors import (
    CaseCollisionError,
    CyclicDependencyError,
    FileOwnershipViolation,
    IllegalNodeTransition,
    OrphanReferenceError,
)
from orchestra.state.adr import ArchitectureDecisionRecord
from orchestra.state.antipatterns import AntipatternEntry, select_relevant_antipatterns
from orchestra.state.contracts import ContractSignature

NodeStatus = Literal[
    "DRAFT",
    "PENDING_DEPS",
    "READY",
    "LEASED",
    "IN_PROGRESS",
    "IN_REVIEW",
    "VERIFYING",
    "NEEDS_REWORK",
    "DONE",
    "BLOCKED_HUMAN",
    "BLOCKED_DISCOVERY",
    "FAILED",
    "CANCELLED",
]

NodeType = Literal[
    "research",
    "design",
    "contract",
    "code",
    "test",
    "integration",
    "docs",
    "gate",
]

ALLOWED_NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    "DRAFT": frozenset({"PENDING_DEPS", "READY", "CANCELLED"}),
    "PENDING_DEPS": frozenset({"READY", "BLOCKED_HUMAN", "CANCELLED"}),
    "READY": frozenset({"LEASED", "PENDING_DEPS", "BLOCKED_HUMAN", "CANCELLED"}),
    "LEASED": frozenset({"IN_PROGRESS", "READY", "BLOCKED_HUMAN", "FAILED", "CANCELLED"}),
    "IN_PROGRESS": frozenset(
        {
            "IN_REVIEW",
            "VERIFYING",
            "READY",
            "BLOCKED_HUMAN",
            "BLOCKED_DISCOVERY",
            "FAILED",
            "CANCELLED",
        }
    ),
    "IN_REVIEW": frozenset({"VERIFYING", "NEEDS_REWORK", "DONE", "BLOCKED_HUMAN", "FAILED"}),
    "VERIFYING": frozenset({"DONE", "NEEDS_REWORK", "IN_REVIEW", "BLOCKED_HUMAN", "FAILED"}),
    "NEEDS_REWORK": frozenset({"READY", "LEASED", "IN_PROGRESS", "FAILED", "CANCELLED"}),
    "DONE": frozenset({"NEEDS_REWORK"}),
    "BLOCKED_HUMAN": frozenset({"READY", "IN_PROGRESS", "PENDING_DEPS", "CANCELLED", "FAILED"}),
    "BLOCKED_DISCOVERY": frozenset({"READY", "IN_PROGRESS", "BLOCKED_HUMAN", "FAILED"}),
    "FAILED": frozenset({"READY", "CANCELLED"}),
    "CANCELLED": frozenset(),
}

ACTIVE_OWNERSHIP_STATUSES: frozenset[NodeStatus] = frozenset(
    {"READY", "LEASED", "IN_PROGRESS", "IN_REVIEW", "VERIFYING", "NEEDS_REWORK"}
)


def normalize_rel_posix_path(raw_path: str) -> str:
    """Normalize a project-relative file path to POSIX forward slashes (WIN-03)."""
    cleaned = raw_path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return str(PurePosixPath(cleaned))


class NodeLease(BaseModel):
    """Exclusive execution lease held by an agent on a MapNode (MM-07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    lease_id: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    @classmethod
    def acquire(
        cls,
        *,
        agent_id: str,
        lease_id: str,
        ttl_s: int = LEASE_TTL_S,
        now: datetime | None = None,
    ) -> NodeLease:
        """Create a fresh NodeLease expiring in `ttl_s` seconds."""
        ts = now or datetime.now(UTC)
        return cls(
            agent_id=agent_id,
            lease_id=lease_id,
            acquired_at=ts,
            heartbeat_at=ts,
            expires_at=ts + timedelta(seconds=ttl_s),
        )

    def heartbeat(self, *, ttl_s: int = LEASE_TTL_S, now: datetime | None = None) -> NodeLease:
        """Extend lease expiration on agent heartbeat."""
        ts = now or datetime.now(UTC)
        return self.model_copy(
            update={"heartbeat_at": ts, "expires_at": ts + timedelta(seconds=ttl_s)}
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return True if the lease TTL has elapsed."""
        ts = now or datetime.now(UTC)
        return ts >= self.expires_at


class AcceptanceCriterion(BaseModel):
    """Verifiable acceptance criterion attached to a MapNode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    verifier: str = "pytest"
    passed: bool = False


class ArtifactRecord(BaseModel):
    """Generated file artifact tracked in MentalMap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    lines: int = 0
    owner_node_id: str
    verified: bool = False


class MapNode(BaseModel):
    """Single task/module vertex in the Mental Map DAG (§17.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    description: str = ""
    type: NodeType = "code"
    status: NodeStatus = "DRAFT"
    owner_role: str = "developer"
    assigned_agent: str | None = None
    dependencies: tuple[str, ...] = ()
    contracts_provided: tuple[str, ...] = ()
    contracts_consumed: tuple[str, ...] = ()
    owned_files: tuple[str, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    edge_cases: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    retry_count: int = 0
    token_budget: int = NODE_TOKEN_LIMIT
    tokens_used: int = 0
    lease: NodeLease | None = None
    rework_defects: tuple[str, ...] = ()
    priority: int = 100

    def transition_to(self, new_status: NodeStatus) -> MapNode:
        """Validate FSM transition and return updated MapNode (MM-01)."""
        if new_status == self.status:
            return self
        allowed = ALLOWED_NODE_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise IllegalNodeTransition(
                f"Illegal transition for node '{self.id}': {self.status} -> {new_status}"
            )
        clear_lease = new_status in ("READY", "DONE", "FAILED", "CANCELLED", "PENDING_DEPS")
        return self.model_copy(
            update={
                "status": new_status,
                "lease": None if clear_lease else self.lease,
            }
        )


class RiskItem(BaseModel):
    """Architectural or operational risk tracked in MentalMap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    mitigation: str
    status: Literal["OPEN", "MITIGATED", "ACCEPTED"] = "OPEN"


class OpenQuestion(BaseModel):
    """Escalated human-in-the-loop question stored in MentalMap (§23)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    node_id: str | None = None
    author_agent: str
    question_ru: str
    options_ru: tuple[str, ...] = ()
    recommendation_idx: int = 0
    impact_ru: str = ""
    status: Literal["OPEN", "ANSWERED", "EXPIRED"] = "OPEN"
    answer: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubmapProjection(BaseModel):
    """Radius-1 local neighborhood projection injected into agent prompts (§17.2, MM-06)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    map_version: int
    focus_node: MapNode
    upstream_nodes: tuple[MapNode, ...]
    downstream_nodes: tuple[MapNode, ...]
    contracts: tuple[ContractSignature, ...]
    antipatterns: tuple[AntipatternEntry, ...]


class MentalMap(BaseModel):
    """Immutable Single Source of Truth (SSOT) project state graph (INV-01, MM-01..15)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    project_name: str
    domain: str = "general"
    version: int = 1
    stage: str = "S0"
    vision: str = ""
    tech_stack: dict[str, str] = Field(default_factory=dict)
    nodes: dict[str, MapNode] = Field(default_factory=dict)
    contracts: dict[str, ContractSignature] = Field(default_factory=dict)
    adrs: dict[str, ArchitectureDecisionRecord] = Field(default_factory=dict)
    antipatterns: tuple[AntipatternEntry, ...] = ()
    risks: tuple[RiskItem, ...] = ()
    open_questions: dict[str, OpenQuestion] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checksum: str = ""

    def compute_checksum(self) -> str:
        """Compute canonical SHA-256 over all fields excluding `checksum` itself (MM-11)."""
        payload = self.model_dump(mode="json", exclude={"checksum"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_updated_checksum(self, *, bump_version: bool = False) -> MentalMap:
        """Return copy with refreshed timestamp, optional version increment, and valid checksum."""
        next_ver = self.version + 1 if bump_version else self.version
        draft = self.model_copy(update={"version": next_ver, "updated_at": datetime.now(UTC)})
        return draft.model_copy(update={"checksum": draft.compute_checksum()})

    def verify_integrity(self) -> None:
        """Verify graph invariants: valid references, acyclic DAG, and exclusive file owners."""
        self._check_references()
        self._check_acyclic()
        self._check_file_ownership_and_casing()

    def _check_references(self) -> None:
        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise OrphanReferenceError(f"Node '{nid}' depends on non-existent node '{dep}'")
            for cid in (*node.contracts_provided, *node.contracts_consumed):
                if cid not in self.contracts:
                    raise OrphanReferenceError(
                        f"Node '{nid}' references non-existent contract '{cid}'"
                    )
        for cid, contract in self.contracts.items():
            if contract.owner_node_id not in self.nodes:
                raise OrphanReferenceError(
                    f"Contract '{cid}' owner_node_id '{contract.owner_node_id}' does not exist"
                )
            for consumer in contract.consumer_node_ids:
                if consumer not in self.nodes:
                    raise OrphanReferenceError(
                        f"Contract '{cid}' consumer '{consumer}' does not exist"
                    )

    def _check_acyclic(self) -> None:
        """Run 3-color DFS to detect cycles and report exact cycle path (MM-03)."""
        color: dict[str, int] = dict.fromkeys(self.nodes, 0)
        parent: dict[str, str] = {}

        def _dfs(curr: str) -> None:
            color[curr] = 1
            for nxt in self.nodes[curr].dependencies:
                if color[nxt] == 1:
                    cycle = [nxt, curr]
                    walk = curr
                    while walk != nxt and walk in parent:
                        walk = parent[walk]
                        cycle.append(walk)
                    cycle.reverse()
                    raise CyclicDependencyError(cycle)
                if color[nxt] == 0:
                    parent[nxt] = curr
                    _dfs(nxt)
            color[curr] = 2

        for node_id in sorted(self.nodes):
            if color[node_id] == 0:
                _dfs(node_id)

    def _check_file_ownership_and_casing(self) -> None:
        """Enforce exclusive file ownership among concurrent nodes and Windows case safety."""
        owners_by_norm: dict[str, tuple[str, str]] = {}
        seen_casefold: dict[str, str] = {}

        for nid in sorted(self.nodes):
            node = self.nodes[nid]
            for raw_file in node.owned_files:
                posix = normalize_rel_posix_path(raw_file)
                folded = posix.casefold()
                prev_exact = seen_casefold.get(folded)
                if prev_exact is not None and prev_exact != posix:
                    raise CaseCollisionError(
                        f"Case-insensitive path collision: '{prev_exact}' vs '{posix}' (WIN-10)"
                    )
                seen_casefold[folded] = posix

                if node.status not in ACTIVE_OWNERSHIP_STATUSES:
                    continue
                prev = owners_by_norm.get(folded)
                if prev is not None and prev[0] != nid:
                    other_id = prev[0]
                    # Concurrent ownership is only legal if one node strictly depends on the other
                    if not (
                        self._depends_transitively(nid, other_id)
                        or self._depends_transitively(other_id, nid)
                    ):
                        raise FileOwnershipViolation(
                            f"File '{posix}' is concurrently owned by independent active nodes "
                            f"'{other_id}' and '{nid}'"
                        )
                owners_by_norm[folded] = (nid, posix)

    def _depends_transitively(self, start_id: str, target_id: str) -> bool:
        visited: set[str] = set()
        stack = list(self.nodes[start_id].dependencies)
        while stack:
            cur = stack.pop()
            if cur == target_id:
                return True
            if cur not in visited and cur in self.nodes:
                visited.add(cur)
                stack.extend(self.nodes[cur].dependencies)
        return False

    def topological_order(self) -> list[str]:
        """Return deterministic Kahn topological ordering of all node IDs."""
        in_degree: dict[str, int] = {nid: len(n.dependencies) for nid, n in self.nodes.items()}
        dependents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                dependents[dep].append(nid)

        ready = sorted(nid for nid, deg in in_degree.items() if deg == 0)
        ordered: list[str] = []
        while ready:
            curr = ready.pop(0)
            ordered.append(curr)
            for child in sorted(dependents[curr]):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    ready.append(child)
                    ready.sort()

        if len(ordered) != len(self.nodes):
            self._check_acyclic()
        return ordered

    def ready_nodes(self) -> list[MapNode]:
        """Return nodes in `READY` status whose dependencies are all `DONE`, sorted by priority."""
        candidates = [
            node
            for node in self.nodes.values()
            if node.status == "READY"
            and all(self.nodes[d].status == "DONE" for d in node.dependencies)
        ]
        return sorted(candidates, key=lambda n: (n.priority, n.id))

    def project_submap(self, node_id: str) -> SubmapProjection:
        """Extract radius-1 neighborhood around `node_id` for context-bounded prompts (MM-06)."""
        focus = self.nodes[node_id]
        upstream = tuple(self.nodes[d] for d in focus.dependencies if d in self.nodes)
        downstream = tuple(n for n in self.nodes.values() if node_id in n.dependencies)
        c_ids = set(focus.contracts_provided) | set(focus.contracts_consumed)
        contracts = tuple(self.contracts[cid] for cid in sorted(c_ids) if cid in self.contracts)
        relevant_ap = tuple(
            select_relevant_antipatterns(
                self.antipatterns,
                tags={focus.type, focus.owner_role},
            )
        )
        return SubmapProjection(
            project_id=self.project_id,
            map_version=self.version,
            focus_node=focus,
            upstream_nodes=upstream,
            downstream_nodes=downstream,
            contracts=contracts,
            antipatterns=relevant_ap,
        )

    def export_mermaid(self) -> str:
        """Render the Mental Map DAG as a valid Mermaid `flowchart TD` diagram (MM-15)."""
        lines = ["flowchart TD"]
        for nid in sorted(self.nodes):
            node = self.nodes[nid]
            safe_title = node.title.replace('"', "'")
            lines.append(f'    {nid}["{nid}: {safe_title} ({node.status})"]')
        for nid in sorted(self.nodes):
            node = self.nodes[nid]
            for dep in node.dependencies:
                lines.append(f"    {dep} --> {nid}")
        return "\n".join(lines)

    def to_summary_dict(self) -> dict[str, Any]:
        """Return compact metrics summary of node statuses."""
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.status] = counts.get(node.status, 0) + 1
        return {
            "project_id": self.project_id,
            "version": self.version,
            "stage": self.stage,
            "total_nodes": len(self.nodes),
            "status_counts": counts,
            "open_questions": sum(1 for q in self.open_questions.values() if q.status == "OPEN"),
        }
