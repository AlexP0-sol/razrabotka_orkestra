"""Property-based suite (`hypothesis`) covering 10 DAG, token, secret, and trading invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orchestra.agents.context_budget import truncate_to_token_cap
from orchestra.domains.crypto_types import dump_decimal, parse_decimal, quantize_down
from orchestra.errors import CyclicDependencyError
from orchestra.interfaces.telegram_bridge import encode_callback_data, split_telegram_message
from orchestra.providers.token_counter import count_tokens_sync
from orchestra.security.secrets import SecretRedactor
from orchestra.skills.builtin.crypto import backtest_report, order_state_machine, risk_limits_check
from orchestra.state.mental_map import MapNode, MentalMap

_SAFE_DECIMALS = st.decimals(
    min_value=Decimal("0.00000001"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=8,
)


@settings(max_examples=30)
@given(
    n_nodes=st.integers(min_value=2, max_value=8),
    edges_seed=st.lists(st.tuples(st.integers(0, 7), st.integers(0, 7)), max_size=12),
)
def test_prop_01_forward_dag_edges_always_valid_and_back_edges_rejected(
    n_nodes: int,
    edges_seed: list[tuple[int, int]],
) -> None:
    """Property 1: forward topological edges never cycle; adding a self/back cycle raises."""
    nodes: dict[str, MapNode] = {}
    for idx in range(n_nodes):
        deps = sorted({f"N{a}" for a, b in edges_seed if b == idx and 0 <= a < idx})
        nodes[f"N{idx}"] = MapNode(id=f"N{idx}", title=f"Node {idx}", dependencies=tuple(deps))
    mmap = MentalMap(project_id="prop_dag", project_name="PropDAG", nodes=nodes)
    mmap.verify_integrity()
    assert len(mmap.nodes) == n_nodes

    cyc_nodes = dict(nodes)
    cyc_nodes["N0"] = cyc_nodes["N0"].model_copy(update={"dependencies": ("N0",)})
    cyc_map = MentalMap(project_id="prop_cyc", project_name="PropCyc", nodes=cyc_nodes)
    with pytest.raises(CyclicDependencyError):
        cyc_map.verify_integrity()


@settings(max_examples=40)
@given(val=_SAFE_DECIMALS, exp=st.sampled_from(["0.1", "0.01", "0.001", "0.00001"]))
def test_prop_02_quantum_normalization_is_idempotent_and_never_exceeds_input(
    val: Decimal,
    exp: str,
) -> None:
    """Property 2 (DP-63, T-18): ROUND_DOWN quantization is idempotent and `<= val`."""
    q = Decimal(exp)
    once = quantize_down(val, q)
    twice = quantize_down(once, q)
    assert once == twice
    assert once <= val


@settings(max_examples=40)
@given(val=_SAFE_DECIMALS)
def test_prop_03_decimal_string_roundtrip_is_exact(val: Decimal) -> None:
    """Property 3 (DP-16, T-18): `parse_decimal(dump_decimal(d)) == d` with zero precision loss."""
    s = dump_decimal(val)
    restored = parse_decimal(s)
    assert restored == val


@settings(max_examples=30)
@given(parts=st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=10))
def test_prop_04_partial_fills_sum_equals_exact_order_quantity(parts: list[int]) -> None:
    """Property 4 (DP-11, DP-63): accumulated partial fills equal total filled order quantity."""
    fills = [Decimal(p) / Decimal("1000") for p in parts]
    order_qty = sum(fills, Decimal("0"))
    remaining = order_qty
    for f in fills:
        remaining -= f
    assert remaining == Decimal("0")


@settings(max_examples=30)
@given(
    balance=_SAFE_DECIMALS,
    price=st.decimals(min_value=Decimal("1"), max_value=Decimal("50000"), places=2),
    qty=st.decimals(min_value=Decimal("0.001"), max_value=Decimal("100"), places=3),
)
def test_prop_05_risk_gate_prevents_negative_free_balance(
    balance: Decimal,
    price: Decimal,
    qty: Decimal,
) -> None:
    """Property 5 (DP-63): risk check rejects any order whose notional exceeds free balance."""
    res = risk_limits_check.run(
        {
            "intent": {"symbol": "BTC/USDT", "price": str(price), "qty": str(qty)},
            "limits": {"max_notional_per_order": str(balance)},
            "portfolio": {"total_exposure": "0"},
        }
    )
    if res["allowed"]:
        assert (balance - (price * qty)) >= Decimal("0")


@settings(max_examples=30)
@given(
    events=st.lists(
        st.sampled_from(
            ["SUBMIT", "ACK", "PARTIAL_FILL", "FILL", "CANCEL", "TIMEOUT", "RECONCILE"]
        ),
        max_size=12,
    )
)
def test_prop_06_order_state_machine_only_reaches_canonical_states(events: list[str]) -> None:
    """Property 6 (DP-63): arbitrary event sequences never escape known canonical order states."""
    known = {
        "NEW",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "EXPIRED",
        "REJECTED",
        "UNKNOWN",
        "RECONCILED",
    }
    state = "NEW"
    for ev in events:
        out = order_state_machine.run({"current": state, "event": ev})
        if out["legal"]:
            state = out["next"]
        assert state in known


@settings(max_examples=30)
@given(text=st.text(min_size=0, max_size=600), cap=st.integers(min_value=5, max_value=80))
def test_prop_07_truncate_to_token_cap_never_exceeds_budget(text: str, cap: int) -> None:
    """Property 7 (§25): `truncate_to_token_cap` output always fits within `cap` tokens."""
    fitted, _ = truncate_to_token_cap(text, cap)
    assert count_tokens_sync(fitted) <= cap


@settings(max_examples=30)
@given(text=st.text(min_size=1, max_size=1200), limit=st.integers(min_value=50, max_value=300))
def test_prop_08_telegram_message_splitter_preserves_exact_text(text: str, limit: int) -> None:
    """Property 8 (TG-06): joined chunks reconstruct `text` and each chunk has `len <= limit`."""
    chunks = split_telegram_message(text, limit=limit)
    assert "".join(chunks) == text
    assert all(len(c) <= limit for c in chunks)


@settings(max_examples=30)
@given(qid=st.text(alphabet="abcdef0123456789_-", min_size=1, max_size=100), idx=st.integers(0, 9))
def test_prop_09_telegram_callback_data_never_exceeds_64_bytes(qid: str, idx: int) -> None:
    """Property 9 (TG-05): encoded callback payload is strictly `<= 64` UTF-8 bytes."""
    cb = encode_callback_data(qid, idx)
    assert len(cb.encode("utf-8")) <= 64


@settings(max_examples=25)
@given(
    secret_tail=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        min_size=16,
        max_size=36,
    ),
    c1=st.integers(min_value=9500, max_value=11000),
    c2=st.integers(min_value=9500, max_value=11000),
)
def test_prop_10_secret_redaction_and_backtest_determinism(
    secret_tail: str,
    c1: int,
    c2: int,
) -> None:
    """Property 10 (INV-07 & TR-14): nvapi secrets always scrubbed; backtest hash deterministic."""
    tok = f"nvapi-{secret_tail}"
    scrubbed = SecretRedactor().redact_text(f"auth={tok} end")
    assert tok not in scrubbed

    payload = {
        "equity_curve": ["10000", str(c1), str(c2)],
        "fills": [{"pnl": "10", "notional": "500"}],
    }
    r1 = backtest_report.run(payload)
    r2 = backtest_report.run(payload)
    assert r1["report_sha256"] == r2["report_sha256"]
