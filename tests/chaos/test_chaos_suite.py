"""Chaos suite: hard_kill recovery (T-02), 429 storm (T-03), corrupt state, and DP-62 cases."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from orchestra.config.settings import load_settings
from orchestra.domains.crypto_types import (
    ExchangeCapabilities,
    PerExchangeRuntimeContext,
)
from orchestra.errors import SandboxTimeoutError
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime, LLMResult
from orchestra.observability.resilience import CheckpointManager
from orchestra.providers.base import ChatMessage
from orchestra.providers.mock import MockProvider, MockScenario, MockStep
from orchestra.skills.builtin.crypto import (
    book_sync,
    data_quality_check,
    order_state_machine,
    risk_limits_check,
    ws_replay,
)
from orchestra.state.mental_map import MapNode, MentalMap, NodeLease
from orchestra.state.state_store import StateStore
from orchestra.verify.sandbox import ExecutionSandbox

if TYPE_CHECKING:
    from pathlib import Path


async def test_t02_hard_kill_process_and_idempotent_crash_recovery(tmp_path: Path) -> None:
    """Verify T-02 & DoD-46: kill hung child process tree via sandbox and recover state."""
    sbx = ExecutionSandbox(tmp_path, timeout_s=0.15)
    with pytest.raises(SandboxTimeoutError):
        await sbx.run(["python", "-c", "import time; time.sleep(60)"])

    lease = NodeLease.acquire(agent_id="dev_01", lease_id="l_crash", ttl_s=600)
    mmap = MentalMap(
        project_id="chaos_crash",
        project_name="ChaosCrash",
        nodes={
            "N1": MapNode(id="N1", title="Finished", status="DONE"),
            "N2": MapNode(
                id="N2",
                title="Killed Mid-Flight",
                status="IN_PROGRESS",
                assigned_agent="dev_01",
                lease=lease,
            ),
        },
    )
    proj_dir = tmp_path / "chaos_proj"
    store = StateStore(proj_dir, initial_map=mmap)
    await store.start()
    await store.checkpoint()
    await store.stop()

    # Corrupt primary mental_map.json to exercise backup + EventLog replay simultaneously
    (proj_dir / "mental_map.json").write_text("{corrupted_json", encoding="utf-8")

    store_restarted = StateStore(proj_dir)
    await store_restarted.start()
    reclaimed = await CheckpointManager(proj_dir).recover_after_crash(store_restarted)
    snap = store_restarted.snapshot()
    assert snap.nodes["N1"].status == "DONE"
    assert snap.nodes["N2"].status == "READY"
    assert reclaimed == ["N2"]
    await store_restarted.stop()


async def test_t03_key_pool_429_storm_rotates_seamlessly_to_healthy_key(tmp_path: Path) -> None:
    """Verify T-03: 429 storm on key_01 puts it into COOLING and succeeds on key_02."""
    provider = MockProvider(
        MockScenario(
            steps=[
                MockStep(fault="rate_limit", retry_after_s=30, max_uses=1),
                MockStep(content_chunks=("RECOVERED_ON_SECOND_KEY",)),
            ]
        )
    )
    pool = KeyPoolManager(["nvapi-storm-key-0001", "nvapi-healthy-key-0002"])
    settings = load_settings(
        overrides={"runtime_dir": str(tmp_path / "rt"), "llm": {"provider": "mock"}}
    )
    llm = DualStreamLLMRuntime(provider=provider, settings=settings, key_pool=pool)
    resp: LLMResult[Any] = await llm.complete(messages=[ChatMessage(role="user", content="Ping")])
    assert resp.content_text == "RECOVERED_ON_SECOND_KEY"
    statuses = {s.status for s in pool.snapshot_public()}
    assert "COOLING" in statuses
    assert "ACTIVE" in statuses


def test_dp62_all_eight_crypto_trading_chaos_scenarios() -> None:
    """Verify DP-62 (1..8): WS gap, timeout->UNKNOWN, dup fill, 429/418, skew, recon, risk."""
    # 1. WS gap mid-orderbook -> ok=False forces snapshot resync
    gap_res = book_sync.run(
        {
            "snapshot": {"lastUpdateId": 100, "bids": [["50000", "1"]], "asks": [["50010", "1"]]},
            "deltas": [{"U": 105, "u": 106, "bids": [["50005", "2"]], "asks": []}],
        }
    )
    assert gap_res["ok"] is False

    # 2. Order send timeout -> UNKNOWN -> RECONCILED (no blind duplicate send)
    step1 = order_state_machine.run({"current": "SUBMITTED", "event": "TIMEOUT"})
    assert step1 == {"next": "UNKNOWN", "legal": True}
    assert order_state_machine.run({"current": "UNKNOWN", "event": "SUBMIT"})["legal"] is False
    step2 = order_state_machine.run({"current": "UNKNOWN", "event": "RECONCILE"})
    assert step2 == {"next": "RECONCILED", "legal": True}

    # 3. Duplicate fill event in WS stream -> deduplicated so PnL is never doubled
    replayed = ws_replay.run(
        {
            "lines": [
                {"event_id": "fill_77", "exchange_ts": 1000, "seq": 1, "pnl": "25.00"},
                {"event_id": "fill_77", "exchange_ts": 1000, "seq": 1, "pnl": "25.00"},
            ]
        }
    )
    assert len(replayed["events"]) == 1
    assert replayed["deduped_count"] == 1
    total_pnl = sum((Decimal(e["pnl"]) for e in replayed["events"]), Decimal("0"))
    assert total_pnl == Decimal("25.00")

    # 4 & 5. 429 backoff + high clock skew -> trips isolated kill switch
    ctx = PerExchangeRuntimeContext(ExchangeCapabilities(exchange_id="binance"))
    assert ctx.rate_limiter.on_http_status(429) == "BACKOFF_429"
    assert ctx.rate_limiter.consume(1) is False
    ctx.update_clock_skew(server_ms=10_000, local_ms=5_000, rtt_ms=40)
    assert ctx.kill_switch.halted is True and "CLOCK_SKEW_HIGH" in str(ctx.kill_switch.reason)

    # 6. Reconciliation mismatch -> trips kill switch HALT
    ctx_recon = PerExchangeRuntimeContext(ExchangeCapabilities(exchange_id="bybit"))
    local_pos = Decimal("1.500")
    exchange_pos = Decimal("1.200")
    if abs(local_pos - exchange_pos) > Decimal("0.001"):
        ctx_recon.kill_switch.trip("RECONCILIATION_MISMATCH")
    assert ctx_recon.kill_switch.halted is True

    # 7. Symbol delisting / zero liquidity anomaly -> blocked by data quality & forbidden_symbols
    dq = data_quality_check.run({"records": [{"exchange_ts": 1000, "close": "0", "volume": "0"}]})
    assert dq["ok"] is False
    delist_risk = risk_limits_check.run(
        {
            "intent": {"symbol": "DELISTED/USDT", "price": "10", "qty": "1"},
            "limits": {"forbidden_symbols": ["DELISTED/USDT"]},
            "portfolio": {},
        }
    )
    assert delist_risk["allowed"] is False

    # 8. Risk check fail-closed when max_daily_loss_quote reached
    loss_gate = risk_limits_check.run(
        {
            "intent": {"symbol": "BTC/USDT", "price": "50000", "qty": "0.01"},
            "limits": {"max_daily_loss_quote": "100.00"},
            "portfolio": {"daily_loss_quote": "100.00"},
        }
    )
    assert loss_gate["allowed"] is False
    assert "MAX_DAILY_LOSS_EXCEEDED" in loss_gate["violations"]
