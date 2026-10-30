"""Unit tests for Phase P11b: CryptoTradingDomainPack, 12 skills, and 13 AST rules."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from orchestra.domains.crypto_ast_verifier import CRYPTO_VERIFIER_RULES, scan_crypto_python_source
from orchestra.domains.crypto_trading import CryptoTradingDomainPack
from orchestra.domains.crypto_types import (
    Amount,
    AssetMismatchError,
    CrossExchangeTimeAligner,
    ExchangeRegistry,
    LiveModeConfirmationError,
    TradingMode,
    assert_live_mode_authorized,
    dump_decimal,
    parse_decimal,
    quantize_down,
)
from orchestra.errors import SkillExecutionError
from orchestra.skills.executor import SkillExecutor
from orchestra.skills.registry import SkillRegistry


def test_domain_pack_seeds_8_professions_with_empty_diaries_and_34_node_map(tmp_path: Path) -> None:
    """Verify DoD-38 & DP-54..55: 8 professions with empty diaries, 12 escalations, 34-node DAG."""
    pack = CryptoTradingDomainPack()
    assert len(pack.role_blueprints()) == 8
    assert len(pack.skill_ids()) == 12
    assert len(pack.mandatory_escalations()) == 12
    blocking = [q.code for q in pack.mandatory_escalations() if q.blocks_s4]
    assert len(blocking) == 5

    seeded_roles = pack.seed_talents(tmp_path / "talents")
    assert len(seeded_roles) == 8
    for role_id in seeded_roles:
        diary = tmp_path / "talents" / role_id / "DISCOVERY_DIARY.md"
        assert diary.is_file()
        assert diary.read_text(encoding="utf-8") == ""

    mmap = pack.build_reference_map()
    assert len(mmap.nodes) == 34
    assert mmap.tech_stack["default_trading_mode"] == "PAPER"
    assert len(mmap.contracts) == 5
    assert all(c.frozen for c in mmap.contracts.values())


async def test_all_12_crypto_skills_seed_execute_and_pass_seeded_tests(tmp_path: Path) -> None:
    """Verify DoD-39 & DP-56: all 12 crypto skills seed with test_skill.py and pass pytest."""
    pack = CryptoTradingDomainPack()
    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir, load_builtins=True)
    seeded = pack.seed_skills(skills_dir, registry=reg)
    assert len(seeded) == 12
    from orchestra.skills.registry import BUILTIN_SKILL_MODULES

    assert len(reg.manifest.skills) == len(BUILTIN_SKILL_MODULES) + 12

    executor = SkillExecutor()
    hmac_spec = reg.get("hmac_sign_request")
    # DP-56: hmac_sign_request rejects raw secret and signs strictly via secret_ref
    with pytest.raises(SkillExecutionError):
        await executor.execute(
            hmac_spec,
            {
                "method": "POST",
                "path": "/order",
                "params": {},
                "secret_ref": "REF1",
                "secret": "RAW_FORBIDDEN",
                "ts": 1,
            },
        )

    good_hmac = await executor.execute(
        hmac_spec,
        {
            "method": "POST",
            "path": "/order",
            "params": {"symbol": "BTCUSDT"},
            "secret_ref": "REF1",
            "ts": 1700000000,
        },
    )
    assert "signature" in good_hmac
    assert "mock-vault-secret" not in str(good_hmac)

    # Run pytest over every seeded `test_skill.py` (12 skills * 6 tests = 72 tests)
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", "--import-mode=importlib", "-q", str(skills_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_all_13_crypto_ast_verifier_rules_trigger_accurately() -> None:
    """Verify DoD-40 & DoD-43: every single one of the 13 §38.13 AST rules catches violations."""
    assert len(CRYPTO_VERIFIER_RULES) == 13

    bad_strategy = (
        "import ccxt\n"
        "import json, time\n"
        "from datetime import datetime\n"
        "price: float = 100.5\n"
        "cross_exchange_pnl: float = 1.2\n"
        "api_secret = 'super_secret_literal_12345'\n"
        "martingale_step = 2\n"
        "now = datetime.utcnow()\n"
        "t0 = time.time()\n"
        "bars = feed.get_bars(symbol='BTC/USDT')\n"
        "fut = df.shift(-1)\n"
        "gw.submit_order(symbol='BTC/USDT', qty=1)\n"
        "raw = json.dumps({'p': float(price)}, default=float)\n"
        "mode = TradingMode.LIVE\n"
        "hdr = 'X-MBX-USED-WEIGHT-1M'\n"
    )
    findings = scan_crypto_python_source(bad_strategy, rel_path="src/strategies/bad_strat.py")
    caught_codes = {f.code for f in findings}
    assert caught_codes == set(CRYPTO_VERIFIER_RULES)

    clean_strategy = (
        "from datetime import UTC, datetime\n"
        "from decimal import Decimal\n"
        "def compute_signal(event_ts: datetime, bar_close: Decimal) -> Decimal:\n"
        "    return bar_close * Decimal('1.00')\n"
    )
    assert scan_crypto_python_source(clean_strategy, rel_path="src/strategies/clean.py") == []


def test_decimal_money_live_guard_and_multi_exchange_extensibility(tmp_path: Path) -> None:
    """Verify TR-01, TR-20, DoD-43, and DoD-49 (add fake exchange via YAML + adapter only)."""
    btc = Amount(value=parse_decimal("0.123456"), asset="BTC")
    usdt = Amount(value=parse_decimal("100.00"), asset="USDT")
    with pytest.raises(AssetMismatchError):
        _ = btc + usdt
    with pytest.raises(TypeError):
        parse_decimal(0.1)  # type: ignore[arg-type]
    assert dump_decimal(quantize_down(Decimal("1.239"), Decimal("0.01"))) == "1.23"

    with pytest.raises(LiveModeConfirmationError):
        assert_live_mode_authorized(TradingMode.LIVE, human_confirmed_live=False)
    assert_live_mode_authorized(
        TradingMode.LIVE,
        human_confirmed_live=True,
        confirmation_text="LIVE",
    )

    # Load root exchanges.yaml -> enabled is empty by default (fail-closed, DP-88)
    root_yaml = Path(__file__).resolve().parents[2] / "exchanges.yaml"
    reg_default = ExchangeRegistry.from_yaml(root_yaml)
    assert reg_default.enabled_exchanges() == []

    # DoD-49: add a brand-new fake exchange purely via exchanges.yaml + gateway/adapters/ file
    custom_yaml = tmp_path / "exchanges.yaml"
    custom_yaml.write_text(
        root_yaml.read_text(encoding="utf-8")
        + "\n  fake_dex:\n"
        + "    adapter_type: native\n"
        + "    adapter_module: gateway.adapters.fake_dex\n"
        + "    markets: [spot, perp]\n"
        + "    signing: Ed25519\n"
        + "    rate_limit_requests_per_min: 600\n",
        encoding="utf-8",
    )
    adapter_file = tmp_path / "gateway" / "adapters" / "fake_dex.py"
    adapter_file.parent.mkdir(parents=True, exist_ok=True)
    adapter_file.write_text(
        "HEADER = 'X-MBX-USED-WEIGHT'\n",
        encoding="utf-8",
    )
    # Exchange-specific header inside gateway/adapters/fake_dex.py must NOT trigger rule 12
    assert (
        scan_crypto_python_source(
            adapter_file.read_text(encoding="utf-8"),
            rel_path="gateway/adapters/fake_dex.py",
        )
        == []
    )

    reg_ext = ExchangeRegistry.from_yaml(custom_yaml)
    ctx_fake = reg_ext.context_for("fake_dex")
    ctx_first = reg_ext.all_exchanges()[0].exchange_id
    ctx_other = reg_ext.context_for(ctx_first)

    # DP-82: tripping 418 IP ban or clock skew on fake_dex never halts other exchanges
    assert ctx_fake.rate_limiter.on_http_status(418) == "HALT_IP_BAN"
    ctx_fake.update_clock_skew(server_ms=5000, local_ms=1000, rtt_ms=20)
    assert ctx_fake.kill_switch.halted is True
    assert ctx_other.kill_switch.halted is False
    assert ctx_other.rate_limiter.consume(1) is True

    aligner = CrossExchangeTimeAligner(tolerance_ms=50)
    t0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    assert aligner.are_aligned(t0, t0 + timedelta(milliseconds=35)) is True
    assert aligner.are_aligned(t0, t0 + timedelta(milliseconds=120)) is False
