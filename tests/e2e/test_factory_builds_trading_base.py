"""E2E flagship test T-16: build multi-exchange crypto trading base (PAPER) with zero leaks."""

from __future__ import annotations

from pathlib import Path

from orchestra.agents.agent_runtime import AgentExecutionResult
from orchestra.agents.roles_catalog import instantiate_charter
from orchestra.config.settings import load_settings
from orchestra.domains.crypto_trading import CryptoTradingDomainPack
from orchestra.domains.crypto_types import ExchangeRegistry
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime
from orchestra.providers.mock import MockProvider
from orchestra.research.pre_study import PreStudyRunner
from orchestra.skills.builtin.crypto import backtest_report, data_quality_check, ws_replay
from orchestra.skills.registry import SkillRegistry
from orchestra.state.state_store import StateStore
from orchestra.swarm.orchestrator import SwarmOrchestrator
from orchestra.talents.talents_store import TalentsStore
from orchestra.verify.git_ops import GitOps
from orchestra.verify.judge import IndependentJudge
from orchestra.verify.patch_engine import CreateFileOp, PatchEngine
from scripts.check_secrets_leak import scan_directory, scan_git_commits

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
ROOT_EXCHANGES_YAML = Path(__file__).resolve().parents[2] / "exchanges.yaml"


async def test_t16_factory_builds_multi_exchange_trading_base_e2e(tmp_path: Path) -> None:
    """Verify T-16, DoD-41, DoD-44, DP-84: 34-node DAG, PAPER mode, 2 exchanges, 0 secret leaks."""
    rt_dir = tmp_path / "runtime"
    ws_dir = tmp_path / "trading_workspace"
    ws_dir.mkdir(parents=True)

    git = GitOps(ws_dir)
    await git.init_workspace_repo(working_branch="orchestra/trading-base")

    settings = load_settings(overrides={"runtime_dir": str(rt_dir), "llm": {"provider": "mock"}})
    pre = PreStudyRunner(docs_cache_dir=settings.paths.docs_cache_dir).run(
        project_id="crypto_base",
        specification_text=(
            "Build multi-exchange ccxt algorithmic strategy platform with Decimal orderbook, "
            "funding, backtest, and kill-switch in PAPER mode"
        ),
    )
    assert pre.detected_domain == "crypto_trading"

    pack = CryptoTradingDomainPack()
    pack.seed_talents(settings.paths.talents_dir)
    skill_reg = SkillRegistry(settings.paths.skills_dir, load_builtins=True)
    pack.seed_skills(settings.paths.skills_dir, registry=skill_reg)

    ref_map = pack.build_reference_map(project_id="crypto_base")
    assert len(ref_map.nodes) == 34
    assert ref_map.tech_stack["default_trading_mode"] == "PAPER"

    pe = PatchEngine(ws_dir)
    pe.apply_transaction(
        [
            CreateFileOp(path="src/__init__.py", content=""),
            CreateFileOp(
                path="src/example_strategy.py",
                content=(
                    '"""EDUCATIONAL EXAMPLE — NOT INVESTMENT ADVICE (DP-66, DP-72)."""\n\n'
                    "from __future__ import annotations\n\n"
                    "from datetime import UTC, datetime\n"
                    "from decimal import Decimal\n\n"
                    'DEFAULT_MODE = "PAPER"\n\n\n'
                    "def evaluate_sma_cross(\n"
                    "    event_ts: datetime,\n"
                    "    fast_sma: Decimal,\n"
                    "    slow_sma: Decimal,\n"
                    ") -> str:\n"
                    '    """Compute deterministic intent without wall-clock or float."""\n'
                    "    if event_ts.tzinfo is None:\n"
                    '        raise ValueError("UTC timezone required")\n'
                    "    if fast_sma > slow_sma:\n"
                    '        return "BUY_INTENT"\n'
                    "    if fast_sma < slow_sma:\n"
                    '        return "SELL_INTENT"\n'
                    '    return "HOLD"\n\n\n'
                    "def epoch_utc() -> datetime:\n"
                    '    """Return UTC epoch anchor."""\n'
                    "    return datetime(2026, 1, 1, tzinfo=UTC)\n"
                ),
            ),
            CreateFileOp(
                path="tests/test_example_strategy.py",
                content=(
                    '"""Verification tests for educational strategy."""\n\n'
                    "from __future__ import annotations\n\n"
                    "from datetime import UTC, datetime\n"
                    "from decimal import Decimal\n\n"
                    "from src.example_strategy import DEFAULT_MODE, evaluate_sma_cross\n\n\n"
                    "def test_paper_default_and_signal() -> None:\n"
                    '    assert DEFAULT_MODE == "PAPER"\n'
                    "    ts = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)\n"
                    '    res_buy = evaluate_sma_cross(ts, Decimal("101.25"), Decimal("100.50"))\n'
                    '    res_sell = evaluate_sma_cross(ts, Decimal("99.75"), Decimal("100.50"))\n'
                    '    assert res_buy == "BUY_INTENT"\n'
                    '    assert res_sell == "SELL_INTENT"\n'
                ),
            ),
        ],
        allowed_owned_files={
            "src/__init__.py",
            "src/example_strategy.py",
            "tests/test_example_strategy.py",
        },
    )

    simulated_secret = "nvapi-" + "SimulatedLiveKeyForRedactionCheck999999"
    pool = KeyPoolManager([simulated_secret])
    store = StateStore(settings.paths.projects_dir / "crypto_base", initial_map=ref_map)
    await store.start()
    llm = DualStreamLLMRuntime(provider=MockProvider(), settings=settings, key_pool=pool)
    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=TalentsStore(settings.paths.talents_dir),
        max_concurrent_agents=6,
    )
    summary = await orch.run_build_loop()
    assert len(summary.completed_nodes) == 34
    assert summary.final_map.stage == "S8_INTEGRATION"

    judge = IndependentJudge(
        ws_dir,
        domain_verifier=pack,
        run_mypy=False,
        mutation_probability=0.0,
    )
    qa_charter = instantiate_charter(
        "qa_adversary",
        instance_index=1,
        overrides={"can_approve_patches": True},
    )
    verdict, _ = await judge.evaluate_node(
        summary.final_map.nodes["N31"].model_copy(
            update={"owned_files": ("src/example_strategy.py",)}
        ),
        AgentExecutionResult(
            agent_id="strategy_quant_01",
            role_id="strategy_quant_engineer",
            node_id="N31",
            content_text="Educational strategy implemented",
            tokens_used=250,
            proposed_payload={"files": ["src/example_strategy.py"]},
        ),
        qa_charter,
    )
    assert verdict.passed is True, verdict.defects

    await git.commit_all("feat(crypto): educational PAPER strategy verified by crypto_ast judge")

    # DP-84 & DP-58: replay fixtures of TWO exchanges (binance + bybit) & check determinism
    ex_reg = ExchangeRegistry.from_yaml(ROOT_EXCHANGES_YAML)
    assert {"binance", "bybit"}.issubset({c.exchange_id for c in ex_reg.all_exchanges()})

    for fixture_name in ("binance_btcusdt_stream.jsonl", "bybit_btcusdt_stream.jsonl"):
        replayed = ws_replay.run({"fixture_path": str(FIXTURES_DIR / fixture_name)})
        assert len(replayed["events"]) == 4
        dq = data_quality_check.run({"records": replayed["events"], "expected_step_ms": 60_000})
        assert dq["ok"] is True

        curve = [ev["close"] for ev in replayed["events"]]
        rep1 = backtest_report.run(
            {"equity_curve": curve, "fills": [{"pnl": "120.00", "notional": "68000.00"}]}
        )
        rep2 = backtest_report.run(
            {"equity_curve": curve, "fills": [{"pnl": "120.00", "notional": "68000.00"}]}
        )
        assert rep1["report_sha256"] == rep2["report_sha256"]

    await store.stop()

    assert scan_directory(rt_dir, extra_exact_secrets=(simulated_secret,)) == []
    assert scan_directory(ws_dir, extra_exact_secrets=(simulated_secret,)) == []
    assert scan_git_commits(ws_dir, extra_exact_secrets=(simulated_secret,)) == []
