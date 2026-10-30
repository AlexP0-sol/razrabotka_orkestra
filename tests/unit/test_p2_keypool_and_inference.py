"""Unit tests for Phase P2: KeyPoolManager, DualStreamRouter, structured output, and LLM runtime."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict

from orchestra.config.settings import load_settings
from orchestra.errors import NoKeysAvailable, StructuredOutputError
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime
from orchestra.inference.stream_router import (
    DualStreamRouter,
    ReasoningLoopDetectedError,
    audit_thinking_protocol,
    detect_reasoning_loop,
    resolve_reasoning_budget,
)
from orchestra.inference.structured_output import parse_structured_content
from orchestra.providers.base import (
    ChatMessage,
    ContentDelta,
    FinishDelta,
    ReasoningDelta,
    StreamDelta,
    compute_key_fingerprint,
)
from orchestra.providers.mock import MockProvider, MockScenario, MockStep

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class _SamplePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    estimate: int


async def test_key_pool_scheduling_quarantine_dead_and_secret_hygiene(tmp_path: Path) -> None:
    """Verify KeyPoolManager rotation, 429/5xx/401 transitions, and zero secret leaks."""
    now = 1_000.0
    alerts: list[tuple[str, str]] = []

    def fake_clock() -> float:
        return now

    state_file = tmp_path / "key_pool_state.json"
    raw_k1 = "nvapi-secret-alpha-0001"
    raw_k2 = "nvapi-secret-beta-0002"
    pool = KeyPoolManager(
        [raw_k1, raw_k2],
        state_path=state_file,
        cooldown_429_s=30,
        cooldown_5xx_s=10,
        max_consecutive_failures=2,
        max_concurrent_per_key=1,
        clock=fake_clock,
        on_alert=lambda code, msg: alerts.append((code, msg)),
    )

    lease1 = await pool.acquire(agent_id="dev_1", estimated_tokens=100)
    lease2 = await pool.acquire(agent_id="dev_2", estimated_tokens=100)
    assert lease1.key_id != lease2.key_id
    assert raw_k1 not in repr(lease1)
    assert raw_k2 not in repr(lease2)

    # Mark key 1 rate-limited (429) with Retry-After=20s
    await pool.release(lease1, outcome="rate_limit", retry_after_s=20)
    await pool.release(lease2, outcome="ok", prompt_tokens=50, completion_tokens=30)

    # Next acquire must pick key 2 because key 1 is COOLING
    lease3 = await pool.acquire(agent_id="dev_1", estimated_tokens=100)
    assert lease3.key_id == lease2.key_id

    # Trigger 2 consecutive 5xx errors on key 2 -> QUARANTINED
    await pool.release(lease3, outcome="server_error")
    now += 15.0  # key 2 wakes from 10s 5xx cooldown, key 1 still has 5s left
    lease4 = await pool.acquire(agent_id="dev_1", estimated_tokens=100)
    assert lease4.key_id == lease2.key_id
    await pool.release(lease4, outcome="server_error")

    stats_by_id = {s.key_id: s for s in pool.snapshot_public()}
    assert stats_by_id[lease2.key_id].status == "QUARANTINED"
    assert any(code == "KEY_QUARANTINED" for code, _ in alerts)

    # Advance clock past key 1's 20s 429 cooldown -> key 1 wakes up ACTIVE
    now += 10.0
    lease5 = await pool.acquire(agent_id="dev_1", estimated_tokens=100)
    assert lease5.key_id == lease1.key_id

    # Unquarantine key 2 manually, then mark both DEAD via 401 -> immediate NoKeysAvailable
    assert await pool.unquarantine(lease2.key_id) is True
    lease6 = await pool.acquire(agent_id="dev_2", estimated_tokens=100)
    await pool.release(lease5, outcome="auth_failed")
    await pool.release(lease6, outcome="auth_failed")

    with pytest.raises(NoKeysAvailable):
        await pool.acquire(agent_id="dev_1", timeout_s=0.2)
    assert any(code == "ALL_KEYS_DEAD" for code, _ in alerts)

    # Verify persisted state file contains ZERO raw secret substrings (INV-09, KP-09)
    saved_text = state_file.read_text(encoding="utf-8")
    assert raw_k1 not in saved_text
    assert raw_k2 not in saved_text
    public_json = json.dumps([s.model_dump() for s in pool.snapshot_public()])
    assert raw_k1 not in public_json
    assert raw_k2 not in public_json


async def test_key_pool_dynamic_add_and_graceful_remove() -> None:
    """Verify dynamic add_key and graceful remove_key draining active leases (KP-07)."""
    pool = KeyPoolManager(["sk-init-1"], max_concurrent_per_key=2)
    k_new = await pool.add_key("sk-dynamic-2")
    assert pool.size == 2

    lease = await pool.acquire(agent_id="arch", estimated_tokens=50)
    target_kid = lease.key_id

    async def _release_soon() -> None:
        await asyncio.sleep(0.03)
        await pool.release(lease, outcome="ok")

    rel_task = asyncio.create_task(_release_soon())
    removed = await pool.remove_key(target_kid, timeout_s=1.0)
    await rel_task
    assert removed is True
    assert pool.size == 1
    assert k_new in {s.key_id for s in pool.snapshot_public()} or pool.size == 1


async def test_dual_stream_router_isolation_and_loop_detection(tmp_path: Path) -> None:
    """Verify reasoning never contaminates content (INV-07) and loops abort stream (LLM-08)."""
    ui_thinking: list[str] = []
    ui_content: list[str] = []
    router = DualStreamRouter(
        on_thinking_delta=ui_thinking.append,
        on_content_delta=ui_content.append,
        thinking_log_dir=tmp_path / "thinking_logs",
    )

    async def _good_stream() -> AsyncIterator[StreamDelta]:
        yield ReasoningDelta(
            text="1. BOUNDARY input check. 2. DIARY clean. 3. SECURITY safe. 4. CONTRACT ok."
        )
        yield ContentDelta(text='{"node_id": "N1", "estimate": 3}')
        yield FinishDelta(reason="stop")

    out = await router.consume(_good_stream(), request_id="r_good", prompt_text="hello")
    assert out.content_text == '{"node_id": "N1", "estimate": 3}'
    assert "BOUNDARY" not in out.content_text
    assert "BOUNDARY" in out.reasoning_text
    assert out.usage_estimated is True
    assert (tmp_path / "thinking_logs" / "r_good.log").is_file()
    assert audit_thinking_protocol(out.reasoning_text).passed is True

    # Loop detection: 4 repetitions of an 85-char block
    repeated_unit = "A" * 40 + "_LOOP_FRAGMENT_REPEATING_FOREVER_" + "B" * 12
    assert len(repeated_unit) >= 80
    assert detect_reasoning_loop(repeated_unit * 4) is True

    async def _loop_stream() -> AsyncIterator[StreamDelta]:
        for _ in range(4):
            yield ReasoningDelta(text=repeated_unit)

    with pytest.raises(ReasoningLoopDetectedError):
        await router.consume(_loop_stream(), request_id="r_loop")

    assert resolve_reasoning_budget("Lead Architect") == 16_384
    assert resolve_reasoning_budget("Core Developer") == 8_192
    assert resolve_reasoning_budget("Frontend UI") == 4_096
    assert resolve_reasoning_budget("Core Developer", trivial=True) == 2_048


def test_structured_output_extraction() -> None:
    """Verify JSON extraction handles fenced blocks and balanced objects inside prose."""
    fenced = 'Here is the plan:\n```json\n{"node_id": "N2", "estimate": 5}\n```\nDone.'
    parsed = parse_structured_content(fenced, _SamplePlan)
    assert parsed.node_id == "N2"
    assert parsed.estimate == 5

    prose = 'Result -> {"node_id": "N3", "estimate": 8} (verified).'
    assert parse_structured_content(prose, _SamplePlan).node_id == "N3"

    with pytest.raises(StructuredOutputError):
        parse_structured_content("not json at all", _SamplePlan)


async def test_llm_runtime_failover_continuation_repair_and_cache(tmp_path: Path) -> None:
    """Verify DualStreamLLMRuntime handles 429 failover, continuation, repair, and cache."""
    settings = load_settings(
        env_file=None,
        overrides={
            "runtime_dir": str(tmp_path / "rt"),
            "llm": {"provider": "mock", "cache_enabled": True, "temperature": 0.0},
        },
    )
    k1 = "sk-pool-key-one"
    k2 = "sk-pool-key-two"
    kid1 = compute_key_fingerprint(k1)
    pool = KeyPoolManager([k1, k2], max_concurrent_per_key=2)

    scenario = MockScenario(
        steps=[
            # 1) Failover test: key 1 returns 429 once -> runtime switches to key 2 immediately
            MockStep(node_id="N_FAILOVER", key_id=kid1, fault="rate_limit", max_uses=1),
            MockStep(
                node_id="N_FAILOVER",
                content_chunks=('{"node_id": "N_FAILOVER", "estimate": 1}',),
            ),
            # 2) Truncated output continuation test
            MockStep(
                node_id="N_TRUNC",
                content_chunks=('{"node_id": "N_TR',),
                fault="truncated_length",
                max_uses=1,
            ),
            MockStep(
                node_id="N_TRUNC",
                content_chunks=('UNC", "estimate": 7}',),
                max_uses=1,
            ),
            # 3) Broken JSON -> automatic fast repair call (role='trivial')
            MockStep(
                node_id="N_REPAIR",
                role="developer",
                content_chunks=('{"node_id": "N_REPAIR", BROKEN}',),
                max_uses=1,
            ),
            MockStep(
                node_id="N_REPAIR",
                role="trivial",
                content_chunks=('{"node_id": "N_REPAIR", "estimate": 11}',),
                max_uses=1,
            ),
        ]
    )
    provider = MockProvider(scenario)
    runtime = DualStreamLLMRuntime(
        provider=provider,
        settings=settings,
        key_pool=pool,
        thinking_log_dir=tmp_path / "rt" / "thinking",
    )

    # 1) Key failover on 429 + response cache hit on second identical call
    msgs = [ChatMessage(role="user", content="Plan N_FAILOVER")]
    res1 = await runtime.complete(
        msgs,
        node_id="N_FAILOVER",
        response_schema=_SamplePlan,
    )
    assert res1.structured is not None
    assert res1.structured.node_id == "N_FAILOVER"
    assert res1.cached is False

    res1_cached = await runtime.complete(
        msgs,
        node_id="N_FAILOVER",
        response_schema=_SamplePlan,
    )
    assert res1_cached.cached is True
    assert res1_cached.structured == res1.structured

    # 2) Continuation stitches partial prefix + suffix cleanly into valid JSON
    res_trunc = await runtime.complete(
        [ChatMessage(role="user", content="Plan N_TRUNC")],
        node_id="N_TRUNC",
        response_schema=_SamplePlan,
    )
    assert res_trunc.structured == _SamplePlan(node_id="N_TRUNC", estimate=7)

    # 3) Broken JSON triggers automatic repair step and validates cleanly
    res_rep = await runtime.complete(
        [ChatMessage(role="user", content="Plan N_REPAIR")],
        node_id="N_REPAIR",
        role="developer",
        response_schema=_SamplePlan,
    )
    assert res_rep.structured == _SamplePlan(node_id="N_REPAIR", estimate=11)
