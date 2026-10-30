"""Unit tests for Phase P5: Telegraphic RPC validator, EventBus, and L1/L2/L3 Escalation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from orchestra.comms.escalation import EscalationManager, EscalationRequest
from orchestra.comms.event_bus import EventBus
from orchestra.comms.rpc_protocol import RpcEnvelope, parse_rpc_envelope
from orchestra.comms.telegraphic import compress_telegraphic, validate_telegraphic_field
from orchestra.errors import RpcExpiredError, RpcSchemaViolation, RpcStyleViolation
from orchestra.state.contracts import ContractSignature
from orchestra.state.mental_map import MapNode, MentalMap
from orchestra.state.state_store import StateStore

if TYPE_CHECKING:
    from pathlib import Path


def test_telegraphic_validator_and_rpc_envelope_rules() -> None:
    """Verify INV-08/LP-06: reject non-ASCII, polite prose, over-budget notes, and bad schemas."""
    # Reject Cyrillic in inter-agent RPC
    with pytest.raises(RpcStyleViolation, match="NON_ASCII"):
        validate_telegraphic_field("Привет агент", field_name="note", max_tokens=40)

    # Reject polite conversational filler (BAN-08)
    with pytest.raises(RpcStyleViolation, match="POLITE_FILLER"):
        RpcEnvelope(
            sender_id="dev_01",
            recipient_id="judge_01",
            msg_type="TASK_SUBMIT",
            note="Please review this patch kindly, thank you!",
            payload={"node_id": "N1", "summary": "IMPL OK"},
        )

    # Reject token overflow in note (> 40 tokens)
    long_note = " ".join(f"TOKEN_{i}" for i in range(60))
    with pytest.raises(RpcStyleViolation, match="TOKEN_LIMIT_EXCEEDED"):
        validate_telegraphic_field(long_note, field_name="note", max_tokens=40)

    compressed = compress_telegraphic("Please check Привет " + long_note, max_tokens=15)
    validate_telegraphic_field(compressed, field_name="note", max_tokens=15)

    # Reject schema mismatch
    with pytest.raises(RpcSchemaViolation):
        parse_rpc_envelope(
            {
                "sender_id": "dev_01",
                "recipient_id": "judge_01",
                "msg_type": "VERIFY_VERDICT",
                "payload": {"wrong_key": 123},
            }
        )

    # Reject expired TTL
    old_env = RpcEnvelope(
        sender_id="dev_01",
        recipient_id="judge_01",
        msg_type="HEARTBEAT",
        ttl_s=5,
        created_at=datetime.now(UTC) - timedelta(seconds=20),
        payload={"node_id": "N1", "lease_id": "L1"},
    )
    with pytest.raises(RpcExpiredError):
        old_env.assert_not_expired()


async def test_event_bus_request_reply_broadcast_and_ring_buffer(tmp_path: Path) -> None:
    """Verify EventBus routes point-to-point, request-reply, broadcast, and logs JSONL."""
    rpc_log = tmp_path / "rpc_messages.jsonl"
    bus = EventBus(rpc_log_path=rpc_log, ring_buffer_size=50)
    bus.register_agent("arch_01")
    bus.register_agent("dev_01")
    bus.register_agent("dev_02")

    async def _arch_responder() -> None:
        req = await bus.receive_rpc("arch_01", timeout_s=2.0)
        reply = RpcEnvelope(
            correlation_id=req.msg_id,
            sender_id="arch_01",
            recipient_id=req.sender_id,
            msg_type="CONTRACT_REPLY",
            payload={
                "contract_id": "C1",
                "version": 1,
                "sha256": "abc123",
                "summary": "FROZEN V1 ACTIVE",
            },
        )
        await bus.send_rpc(reply)

    responder_task = asyncio.create_task(_arch_responder())
    query = RpcEnvelope(
        sender_id="dev_01",
        recipient_id="arch_01",
        msg_type="CONTRACT_QUERY",
        payload={"contract_id": "C1", "question_code": "CHECK_RET_TYPE"},
    )
    reply_env = await bus.request_reply(query, timeout_s=2.0)
    await responder_task
    assert reply_env.payload["summary"] == "FROZEN V1 ACTIVE"

    # Broadcast reaches both dev_01 and dev_02, excluding sender arch_01
    bcast = RpcEnvelope(
        sender_id="arch_01",
        recipient_id="*",
        msg_type="BROADCAST_EVENT",
        payload={"event_code": "GATE_G1_PASS", "summary": "CONTRACTS FROZEN"},
    )
    await bus.send_rpc(bcast)
    r1 = await bus.receive_rpc("dev_01", timeout_s=1.0)
    r2 = await bus.receive_rpc("dev_02", timeout_s=1.0)
    assert r1.msg_id == bcast.msg_id == r2.msg_id
    assert len(bus.recent_events(since_seq=0)) >= 3
    assert rpc_log.is_file()


async def test_escalation_manager_l1_l2_l3_cap_and_timeout(tmp_path: Path) -> None:
    """Verify L1/L2 local resolution, L3 Russian cards, MAX_OPEN_QUESTIONS=3 cap, and unblocking."""
    c1 = ContractSignature.create(
        contract_id="C1",
        owner_node_id="N1",
        definition="def calc() -> Decimal: ...",
    )
    init_map = MentalMap(
        project_id="p_esc",
        project_name="Esc",
        nodes={
            f"N{i}": MapNode(
                id=f"N{i}",
                title=f"Node {i}",
                status="READY",
                contracts_provided=("C1",) if i == 1 else (),
            )
            for i in range(1, 6)
        },
        contracts={"C1": c1},
    ).with_updated_checksum()

    store = StateStore(tmp_path / "proj", initial_map=init_map)
    await store.start()
    bus = EventBus()
    mgr = EscalationManager(
        state_store=store,
        event_bus=bus,
        max_open_questions=3,
        question_timeout_s=60,
        timeout_policy="apply_recommendation",
    )

    # L1 resolved immediately via MentalMap contract
    r_l1 = await mgr.escalate(
        EscalationRequest(
            author_agent="dev_01",
            contract_id="C1",
            is_business_decision=False,
            question_ru="Какой тип возврата?",
        )
    )
    assert r_l1.level == "L1_PEER_CONTRACT"
    assert r_l1.resolved_immediately is True

    # L2 resolved immediately via Architect recommendation
    r_l2 = await mgr.escalate(
        EscalationRequest(
            author_agent="dev_01",
            is_business_decision=False,
            question_ru="Выбрать структуру индекса?",
            options_ru=("B-Tree", "Hash"),
            recommendation_idx=0,
        )
    )
    assert r_l2.level == "L2_ARCHITECT"
    assert r_l2.answer == "B-Tree"

    # L3 business forks: open 4 questions -> 3 become OPEN, 4th queues in backlog!
    q_ids: list[str] = []
    for idx in range(1, 5):
        res = await mgr.escalate(
            EscalationRequest(
                author_agent="lead_architect_01",
                node_id=f"N{idx}",
                is_business_decision=True,
                question_ru=f"Бизнес-развилка #{idx}?",
                options_ru=("Консервативный режим", "Агрессивный режим"),
                recommendation_idx=0,
            )
        )
        if res.question_id:
            q_ids.append(res.question_id)
        else:
            assert res.queued_behind_cap is True

    assert len(q_ids) == 3
    assert mgr.backlog_size == 1
    assert store.snapshot().nodes["N1"].status == "BLOCKED_HUMAN"

    # Answering Q1 unblocks N1 -> READY and promotes 4th question from backlog!
    await mgr.answer_question(q_ids[0], "Консервативный режим")
    assert store.snapshot().nodes["N1"].status == "READY"
    assert mgr.backlog_size == 0

    # Expire remaining open questions with apply_recommendation policy
    expired = await mgr.expire_overdue_questions(now=datetime.now(UTC) + timedelta(seconds=120))
    assert len(expired) == 3
    assert store.snapshot().nodes["N2"].status == "READY"
    await store.stop()
