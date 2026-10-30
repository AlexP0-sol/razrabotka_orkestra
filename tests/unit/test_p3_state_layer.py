"""Unit tests for Phase P3: MentalMap DAG invariants, Contracts, EventLog, and StateStore."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from orchestra.errors import (
    CaseCollisionError,
    ContractFrozenError,
    CyclicDependencyError,
    FileOwnershipViolation,
    IllegalNodeTransition,
    OrphanReferenceError,
    StaleStateError,
)
from orchestra.state.adr import ArchitectureDecisionRecord
from orchestra.state.antipatterns import (
    AntipatternEntry,
    format_antipatterns_compact,
    select_relevant_antipatterns,
)
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import MapNode, MentalMap
from orchestra.state.state_store import StateStore

if TYPE_CHECKING:
    from pathlib import Path


def _seed_map() -> MentalMap:
    c1 = ContractSignature.create(
        contract_id="C_API",
        owner_node_id="N1",
        consumer_node_ids=("N2",),
        definition="class ApiProtocol(Protocol):\n    def fetch(self) -> int: ...",
    )
    n1 = MapNode(
        id="N1",
        title="Core API",
        status="READY",
        contracts_provided=("C_API",),
        owned_files=("src/core/api.py",),
    )
    n2 = MapNode(
        id="N2",
        title="Service Layer",
        status="PENDING_DEPS",
        dependencies=("N1",),
        contracts_consumed=("C_API",),
        owned_files=("src/service/runner.py",),
    )
    return MentalMap(
        project_id="proj_test",
        project_name="Test Project",
        nodes={"N1": n1, "N2": n2},
        contracts={"C_API": c1},
    ).with_updated_checksum()


def test_mental_map_cycle_orphan_ownership_and_case_collision() -> None:
    """Verify MentalMap enforces DAG acyclicity, valid refs, file ownership, and Windows casing."""
    # 1) Cycle detection N1 -> N2 -> N3 -> N1
    cyclic = MentalMap(
        project_id="p_cyc",
        project_name="Cyclic",
        nodes={
            "N1": MapNode(id="N1", title="A", dependencies=("N3",)),
            "N2": MapNode(id="N2", title="B", dependencies=("N1",)),
            "N3": MapNode(id="N3", title="C", dependencies=("N2",)),
        },
    )
    with pytest.raises(CyclicDependencyError) as cyc_exc:
        cyclic.verify_integrity()
    assert len(cyc_exc.value.cycle_path) >= 3

    # 2) Orphan dependency
    orphan = MentalMap(
        project_id="p_orph",
        project_name="Orphan",
        nodes={"N1": MapNode(id="N1", title="A", dependencies=("MISSING",))},
    )
    with pytest.raises(OrphanReferenceError):
        orphan.verify_integrity()

    # 3) Concurrent independent ownership of same file
    overlap = MentalMap(
        project_id="p_own",
        project_name="Overlap",
        nodes={
            "N1": MapNode(id="N1", title="A", status="READY", owned_files=("src/shared.py",)),
            "N2": MapNode(id="N2", title="B", status="READY", owned_files=("src/shared.py",)),
        },
    )
    with pytest.raises(FileOwnershipViolation):
        overlap.verify_integrity()

    # 4) Case-insensitive path collision on Windows/macOS (WIN-10)
    collision = MentalMap(
        project_id="p_case",
        project_name="Case",
        nodes={
            "N1": MapNode(id="N1", title="A", status="DONE", owned_files=("src/OrderBook.py",)),
            "N2": MapNode(id="N2", title="B", status="READY", owned_files=("src/orderbook.py",)),
        },
    )
    with pytest.raises(CaseCollisionError):
        collision.verify_integrity()

    # 5) Illegal FSM transition
    node = MapNode(id="N1", title="A", status="DRAFT")
    with pytest.raises(IllegalNodeTransition):
        node.transition_to("DONE")


def test_adr_antipatterns_submap_and_mermaid() -> None:
    """Verify ADR Markdown rendering, antipattern ranking, submap projection, and Mermaid export."""
    adr = ArchitectureDecisionRecord(
        id="ADR-0010",
        title="Use Async Queue",
        context="Need single writer",
        decision="Use asyncio.Queue",
        consequences="Zero race conditions",
        alternatives=("Thread locks",),
    )
    md = adr.to_markdown()
    assert "# ADR-0010: Use Async Queue" in md
    assert "Thread locks" in md

    ap1 = AntipatternEntry(
        id="AP-1",
        symptom="Float drift",
        root_cause="Binary float",
        forbidden_pattern="float(price)",
        safe_alternative="Decimal(str(price))",
        severity="CRITICAL",
        tags=("code", "crypto"),
    )
    ap2 = AntipatternEntry(
        id="AP-2",
        symptom="Minor lint",
        root_cause="Style",
        forbidden_pattern="unused var",
        safe_alternative="prefix _",
        severity="LOW",
        tags=("docs",),
    )
    base = _seed_map().model_copy(update={"adrs": {"ADR-0010": adr}, "antipatterns": (ap2, ap1)})
    selected = select_relevant_antipatterns(base.antipatterns, tags={"code"})
    assert selected[0].id == "AP-1"
    assert "Decimal(str(price))" in format_antipatterns_compact(selected)

    sub = base.project_submap("N1")
    assert sub.focus_node.id == "N1"
    assert [n.id for n in sub.downstream_nodes] == ["N2"]
    assert [c.id for c in sub.contracts] == ["C_API"]

    mermaid = base.export_mermaid()
    assert mermaid.startswith("flowchart TD")
    assert "N1 --> N2" in mermaid
    assert base.topological_order() == ["N1", "N2"]


async def test_state_store_single_writer_contracts_leases_and_crash_recovery(
    tmp_path: Path,
) -> None:
    """Verify StateStore single-writer concurrency, contract freeze, leases, and crash recovery."""
    proj_dir = tmp_path / "proj"
    store = StateStore(
        proj_dir,
        initial_map=_seed_map(),
        snapshot_every_events=2,
        snapshot_backups=3,
        lease_ttl_s=60,
    )
    snap0 = await store.start()
    assert snap0.version == 1

    # Optimistic concurrency mismatch raises StaleStateError
    with pytest.raises(StaleStateError):
        await store.mutate(
            actor="arch",
            mutation_type="set_stage",
            payload={"stage": "S3"},
            expected_version=999,
        )

    # Freeze C_API -> modifying it without unfreeze must raise ContractFrozenError
    await store.mutate(actor="arch", mutation_type="freeze_contracts", payload={})
    frozen_c = store.snapshot().contracts["C_API"]
    assert frozen_c.frozen is True
    with pytest.raises(ContractFrozenError):
        await store.mutate(
            actor="dev_1",
            mutation_type="upsert_contract",
            payload={
                "contract": ContractSignature.create(
                    contract_id="C_API",
                    owner_node_id="N1",
                    definition="class Changed: ...",
                ).model_dump(mode="json")
            },
        )

    # Lease N1 -> IN_PROGRESS -> VERIFYING -> DONE; verify N2 auto-promotes PENDING_DEPS -> READY!
    _, lease = await store.acquire_node_lease(node_id="N1", agent_id="dev_1")
    assert store.snapshot().nodes["N1"].status == "LEASED"
    await store.mutate(
        actor="dev_1",
        mutation_type="transition_node",
        payload={"node_id": "N1", "status": "IN_PROGRESS", "tokens_used_delta": 1200},
    )
    await store.mutate(
        actor="dev_1",
        mutation_type="transition_node",
        payload={"node_id": "N1", "status": "VERIFYING"},
    )
    after_done = await store.mutate(
        actor="judge",
        mutation_type="transition_node",
        payload={"node_id": "N1", "status": "DONE"},
    )
    assert after_done.nodes["N1"].status == "DONE"
    assert after_done.nodes["N2"].status == "READY"

    # Lease N2, then expire lease via reap_expired_leases
    _, n2_lease = await store.acquire_node_lease(node_id="N2", agent_id="dev_2")
    future_ts = n2_lease.expires_at + timedelta(seconds=5)
    after_reap = await store.reap_expired_leases(now=future_ts)
    assert after_reap.nodes["N2"].status == "READY"
    assert after_reap.nodes["N2"].retry_count == 1

    # Complete N2 -> DONE, then Architect unfreezes C_API -> consumer N2 moves to NEEDS_REWORK!
    await store.acquire_node_lease(node_id="N2", agent_id="dev_2")
    await store.mutate(
        actor="dev_2",
        mutation_type="transition_node",
        payload={"node_id": "N2", "status": "IN_PROGRESS"},
    )
    await store.mutate(
        actor="dev_2",
        mutation_type="transition_node",
        payload={"node_id": "N2", "status": "VERIFYING"},
    )
    await store.mutate(
        actor="judge",
        mutation_type="transition_node",
        payload={"node_id": "N2", "status": "DONE"},
    )
    unfrozen_map = await store.mutate(
        actor="arch",
        mutation_type="unfreeze_contract",
        payload={"contract_id": "C_API", "reason": "Add async batch method"},
    )
    assert unfrozen_map.contracts["C_API"].frozen is False
    assert unfrozen_map.contracts["C_API"].version == 2
    assert unfrozen_map.nodes["N2"].status == "NEEDS_REWORK"

    # Parallel mutations serialized cleanly without race conditions
    await asyncio.gather(
        *(
            store.mutate(
                actor=f"agent_{i}",
                mutation_type="upsert_risk",
                payload={
                    "risk": {
                        "id": f"R_{i}",
                        "description": f"Risk {i}",
                        "severity": "LOW",
                        "mitigation": "Mitigated",
                        "status": "OPEN",
                    }
                },
            )
            for i in range(5)
        )
    )
    final_version = store.snapshot().version
    await store.stop()

    # Corrupt primary mental_map.json AND append a truncated power-loss tail to events.jsonl!
    (proj_dir / "mental_map.json").write_text("{corrupted_json!!!", encoding="utf-8")
    with (proj_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"seq": 9999, "broken_tail": ')

    # Restart StateStore -> must fall back to .bak.1 and replay events up to final_version!
    recovered_store = StateStore(proj_dir)
    recovered_map = await recovered_store.start()
    assert recovered_map.version == final_version
    assert len(recovered_map.risks) == 5
    await recovered_store.stop()
    _ = lease
    _ = datetime.now(UTC)
