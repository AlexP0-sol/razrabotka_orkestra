"""Single-Writer Actor StateStore serializing all MentalMap mutations (§6.1, §13.5, INV-01)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orchestra.config.constants import LEASE_TTL_S, SNAPSHOT_BACKUPS, SNAPSHOT_EVERY_EVENTS
from orchestra.errors import StaleStateError, StateError
from orchestra.state.adr import ArchitectureDecisionRecord
from orchestra.state.antipatterns import AntipatternEntry
from orchestra.state.contracts import ContractSignature
from orchestra.state.event_log import EventLog, StateEvent
from orchestra.state.mental_map import (
    ArtifactRecord,
    MapNode,
    MentalMap,
    NodeLease,
    NodeStatus,
    OpenQuestion,
    RiskItem,
)

if TYPE_CHECKING:
    from pathlib import Path

StateSubscriber = Callable[[MentalMap, StateEvent], Awaitable[None] | None]


@dataclass
class _WriteCommand:
    actor: str
    mutation_type: str
    expected_version: int | None
    payload: dict[str, Any]
    future: asyncio.Future[MentalMap]


def apply_mutation_payload(
    current: MentalMap,
    mutation_type: str,
    payload: dict[str, Any],
) -> MentalMap:
    """Pure deterministic state reducer used both by live writes and EventLog replay (MM-12)."""
    nodes = dict(current.nodes)
    contracts = dict(current.contracts)
    adrs = dict(current.adrs)
    questions = dict(current.open_questions)
    antipatterns = list(current.antipatterns)
    risks = list(current.risks)
    stage = current.stage
    vision = current.vision
    tech_stack = dict(current.tech_stack)

    if mutation_type == "set_stage":
        stage = str(payload["stage"])
        if "vision" in payload:
            vision = str(payload["vision"])
        if "tech_stack" in payload:
            tech_stack = dict(payload["tech_stack"])
    elif mutation_type == "upsert_nodes":
        for raw_node in payload["nodes"]:
            node = MapNode.model_validate(raw_node)
            nodes[node.id] = node
    elif mutation_type == "transition_node":
        nid = str(payload["node_id"])
        if nid not in nodes:
            raise StateError(f"Unknown node_id '{nid}'")
        target: NodeStatus = payload["status"]
        updated = nodes[nid].transition_to(target)
        if "rework_defects" in payload:
            updated = updated.model_copy(
                update={"rework_defects": tuple(payload["rework_defects"])}
            )
        if "retry_count" in payload:
            updated = updated.model_copy(update={"retry_count": int(payload["retry_count"])})
        if "tokens_used_delta" in payload:
            updated = updated.model_copy(
                update={"tokens_used": updated.tokens_used + int(payload["tokens_used_delta"])}
            )
        if "artifacts" in payload:
            arts = tuple(ArtifactRecord.model_validate(a) for a in payload["artifacts"])
            updated = updated.model_copy(update={"artifacts": arts})
        nodes[nid] = updated
    elif mutation_type == "acquire_lease":
        nid = str(payload["node_id"])
        if nid not in nodes:
            raise StateError(f"Unknown node_id '{nid}'")
        node = nodes[nid]
        lease = NodeLease.model_validate(payload["lease"])
        leased_node = node.transition_to("LEASED").model_copy(
            update={"lease": lease, "assigned_agent": lease.agent_id}
        )
        nodes[nid] = leased_node
    elif mutation_type == "heartbeat_lease":
        nid = str(payload["node_id"])
        node = nodes[nid]
        if node.lease is None or node.lease.lease_id != payload["lease_id"]:
            raise StateError(f"Invalid lease heartbeat on node '{nid}'")
        nodes[nid] = node.model_copy(
            update={"lease": node.lease.heartbeat(ttl_s=int(payload["ttl_s"]))}
        )
    elif mutation_type == "reap_expired_leases":
        now = datetime.fromisoformat(str(payload["now_iso"]))
        for nid, node in list(nodes.items()):
            if (
                node.lease is not None
                and node.lease.is_expired(now=now)
                and node.status in ("LEASED", "IN_PROGRESS")
            ):
                nodes[nid] = node.model_copy(
                    update={
                        "status": "READY",
                        "lease": None,
                        "retry_count": node.retry_count + 1,
                    }
                )
    elif mutation_type == "upsert_contract":
        incoming = ContractSignature.model_validate(payload["contract"])
        existing = contracts.get(incoming.id)
        if existing is not None and existing.frozen:
            existing.update_definition(incoming.definition)
        contracts[incoming.id] = incoming
    elif mutation_type == "freeze_contracts":
        cids = payload.get("contract_ids") or list(contracts.keys())
        for cid in cids:
            if cid in contracts:
                contracts[cid] = contracts[cid].freeze()
    elif mutation_type == "unfreeze_contract":
        cid = str(payload["contract_id"])
        reason = str(payload["reason"])
        unfrozen = contracts[cid].unfreeze(reason=reason)
        contracts[cid] = unfrozen
        for consumer_id in unfrozen.consumer_node_ids:
            c_node = nodes.get(consumer_id)
            if c_node is not None and c_node.status in ("DONE", "IN_REVIEW", "VERIFYING"):
                nodes[consumer_id] = c_node.transition_to("NEEDS_REWORK").model_copy(
                    update={
                        "rework_defects": (
                            *c_node.rework_defects,
                            f"CONTRACT_UNFROZEN:{cid}:v{unfrozen.version}",
                        )
                    }
                )
    elif mutation_type == "upsert_adr":
        adr = ArchitectureDecisionRecord.model_validate(payload["adr"])
        adrs[adr.id] = adr
    elif mutation_type == "add_antipattern":
        ap = AntipatternEntry.model_validate(payload["antipattern"])
        antipatterns.append(ap)
    elif mutation_type == "upsert_risk":
        risk = RiskItem.model_validate(payload["risk"])
        risks = [r for r in risks if r.id != risk.id]
        risks.append(risk)
    elif mutation_type == "upsert_question":
        q = OpenQuestion.model_validate(payload["question"])
        questions[q.id] = q
    else:
        raise StateError(f"Unsupported mutation_type: {mutation_type}")

    # Auto-promote PENDING_DEPS -> READY whenever all dependencies become DONE
    for nid, node in list(nodes.items()):
        if node.status == "PENDING_DEPS" and all(
            nodes[d].status == "DONE" for d in node.dependencies
        ):
            nodes[nid] = node.transition_to("READY")

    draft = current.model_copy(
        update={
            "stage": stage,
            "vision": vision,
            "tech_stack": tech_stack,
            "nodes": nodes,
            "contracts": contracts,
            "adrs": adrs,
            "antipatterns": tuple(antipatterns),
            "risks": tuple(risks),
            "open_questions": questions,
        }
    )
    next_map = draft.with_updated_checksum(bump_version=True)
    next_map.verify_integrity()
    return next_map


class StateStore:
    """Single-Writer Actor owning the authoritative `MentalMap` in memory and on disk."""

    def __init__(
        self,
        project_dir: Path,
        *,
        initial_map: MentalMap | None = None,
        snapshot_every_events: int = SNAPSHOT_EVERY_EVENTS,
        snapshot_backups: int = SNAPSHOT_BACKUPS,
        lease_ttl_s: int = LEASE_TTL_S,
    ) -> None:
        """Initialize StateStore; call `await start()` before submitting mutations."""
        self._project_dir = project_dir
        self._event_log = EventLog(project_dir, backups=snapshot_backups)
        self._snapshot_every = max(1, snapshot_every_events)
        self._lease_ttl_s = lease_ttl_s
        self._queue: asyncio.Queue[_WriteCommand | None] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        self._subscribers: list[StateSubscriber] = []
        self._events_since_snapshot = 0
        self._seq = 0
        self._initial_seed = initial_map
        self._current: MentalMap | None = None

    @property
    def event_log(self) -> EventLog:
        """Return underlying EventLog instance."""
        return self._event_log

    async def start(self) -> MentalMap:
        """Recover existing state or persist initial MentalMap and launch single-writer task."""
        recovered = self._event_log.recover(
            lambda m, ev: apply_mutation_payload(m, ev.mutation_type, ev.payload)
        )
        if recovered is not None:
            self._current = recovered
            self._seq = recovered.version - 1
        elif self._initial_seed is not None:
            checked = self._initial_seed.with_updated_checksum(bump_version=False)
            checked.verify_integrity()
            self._event_log.write_snapshot(checked)
            self._current = checked
            self._seq = checked.version - 1
        else:
            raise StateError(
                f"No existing MentalMap in {self._project_dir} and no initial_map supplied"
            )

        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(
                self._writer_loop(), name=f"state-writer-{self._current.project_id}"
            )
        return self._current

    async def stop(self) -> None:
        """Flush final snapshot and stop the single-writer actor cleanly."""
        if self._writer_task is not None and not self._writer_task.done():
            await self._queue.put(None)
            await self._writer_task
        if self._current is not None:
            self._event_log.write_snapshot(self._current)
            self._events_since_snapshot = 0

    def snapshot(self) -> MentalMap:
        """Lock-free O(1) read of the latest immutable MentalMap snapshot (CC-06)."""
        if self._current is None:
            raise StateError("StateStore has not been started")
        return self._current

    def subscribe(self, callback: StateSubscriber) -> None:
        """Register an async or sync subscriber notified after every committed mutation."""
        self._subscribers.append(callback)

    async def mutate(
        self,
        *,
        actor: str,
        mutation_type: str,
        payload: dict[str, Any],
        expected_version: int | None = None,
    ) -> MentalMap:
        """Enqueue a mutation to the Single-Writer Actor and await the committed MentalMap."""
        if self._writer_task is None or self._writer_task.done():
            raise StateError("StateStore writer loop is not running")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[MentalMap] = loop.create_future()
        cmd = _WriteCommand(
            actor=actor,
            mutation_type=mutation_type,
            expected_version=expected_version,
            payload=payload,
            future=fut,
        )
        await self._queue.put(cmd)
        return await fut

    async def acquire_node_lease(
        self,
        *,
        node_id: str,
        agent_id: str,
        expected_version: int | None = None,
    ) -> tuple[MentalMap, NodeLease]:
        """Atomically acquire an exclusive lease on `node_id` for `agent_id` (MM-07)."""
        lease = NodeLease.acquire(
            agent_id=agent_id,
            lease_id=f"nlease_{uuid.uuid4().hex[:10]}",
            ttl_s=self._lease_ttl_s,
        )
        new_map = await self.mutate(
            actor=agent_id,
            mutation_type="acquire_lease",
            payload={"node_id": node_id, "lease": lease.model_dump(mode="json")},
            expected_version=expected_version,
        )
        return new_map, lease

    async def reap_expired_leases(self, *, now: datetime | None = None) -> MentalMap:
        """Return expired LEASED/IN_PROGRESS nodes back to READY and increment retry_count."""
        ts = now or datetime.now(UTC)
        return await self.mutate(
            actor="scheduler",
            mutation_type="reap_expired_leases",
            payload={"now_iso": ts.isoformat()},
        )

    async def checkpoint(self) -> None:
        """Force immediate snapshot rotation on disk."""
        if self._current is not None:
            self._event_log.write_snapshot(self._current)
            self._events_since_snapshot = 0

    async def _writer_loop(self) -> None:
        while True:
            cmd = await self._queue.get()
            if cmd is None:
                self._queue.task_done()
                break
            try:
                assert self._current is not None
                if (
                    cmd.expected_version is not None
                    and cmd.expected_version != self._current.version
                ):
                    raise StaleStateError(
                        f"Optimistic lock conflict: expected version {cmd.expected_version}, "
                        f"actual {self._current.version}"
                    )
                next_map = apply_mutation_payload(
                    self._current,
                    cmd.mutation_type,
                    cmd.payload,
                )
                self._seq += 1
                ev = StateEvent(
                    seq=self._seq,
                    event_id=f"ev_{uuid.uuid4().hex[:12]}",
                    actor=cmd.actor,
                    mutation_type=cmd.mutation_type,
                    expected_version=self._current.version,
                    new_version=next_map.version,
                    payload=cmd.payload,
                )
                persisted_ev = self._event_log.append_event(ev)
                self._current = next_map
                self._events_since_snapshot += 1
                if self._events_since_snapshot >= self._snapshot_every:
                    self._event_log.write_snapshot(self._current)
                    self._events_since_snapshot = 0

                if not cmd.future.done():
                    cmd.future.set_result(next_map)
                await self._notify_subscribers(next_map, persisted_ev)
            except Exception as exc:
                if not cmd.future.done():
                    cmd.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _notify_subscribers(self, new_map: MentalMap, event: StateEvent) -> None:
        for sub in list(self._subscribers):
            res = sub(new_map, event)
            if res is not None:
                await res
