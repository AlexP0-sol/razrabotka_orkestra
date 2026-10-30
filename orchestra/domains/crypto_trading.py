"""Crypto Trading & Algorithmic Strategies Domain Pack (§38, DoD-38..DoD-44, DoD-49)."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from orchestra.domains.base import DomainRoleBlueprint, EscalationQuestionTemplate
from orchestra.domains.crypto_ast_verifier import verify_crypto_domain_files
from orchestra.domains.crypto_blueprints import (
    CRYPTO_ROLE_BLUEPRINTS,
    CRYPTO_SKILL_MODULES,
    MANDATORY_CRYPTO_ESCALATIONS,
    REFERENCE_CRYPTO_NODES,
)
from orchestra.state.contracts import ContractSignature
from orchestra.state.event_log import atomic_write_text
from orchestra.state.mental_map import AcceptanceCriterion, MapNode, MentalMap
from orchestra.talents.talents_store import TalentProfile, TalentsStore

if TYPE_CHECKING:
    from orchestra.skills.registry import SkillRegistry
    from orchestra.verify.verifiers import VerifierFinding

__all__ = [
    "CRYPTO_ROLE_BLUEPRINTS",
    "CRYPTO_SKILL_MODULES",
    "MANDATORY_CRYPTO_ESCALATIONS",
    "REFERENCE_CRYPTO_NODES",
    "CryptoTradingDomainPack",
]

_SKILL_SAMPLE_INPUTS: dict[str, dict[str, Any]] = {
    "exchange_symbol_info": {"exchange": "binance", "symbol": "BTC/USDT"},
    "decimal_money": {"value": "42123.45678", "quantum": "0.01", "mode": "ROUND_DOWN"},
    "book_sync": {
        "snapshot": {"lastUpdateId": 10, "bids": [["100.0", "1.0"]], "asks": [["101.0", "1.0"]]},
        "deltas": [{"U": 11, "u": 12, "bids": [["100.5", "2.0"]], "asks": []}],
    },
    "order_state_machine": {"current": "SUBMITTED", "event": "TIMEOUT"},
    "ws_replay": {
        "lines": [
            {"event_id": "e2", "exchange_ts": 200, "seq": 2},
            {"event_id": "e1", "exchange_ts": 100, "seq": 1},
            {"event_id": "e1", "exchange_ts": 100, "seq": 1},
        ]
    },
    "clock_skew_check": {"server_time": 1000250, "local_time": 1000000, "rtt_ms": 100},
    "hmac_sign_request": {
        "method": "POST",
        "path": "/api/v3/order",
        "params": {"symbol": "BTCUSDT", "side": "BUY"},
        "secret_ref": "ORCHESTRA_TEST_EXCHANGE_SECRET_REF",
        "ts": 1700000000000,
    },
    "audit_log_append": {"entry": {"clientOrderId": "strat-01-001", "action": "PRE_SEND"}},
    "risk_limits_check": {
        "intent": {"symbol": "BTC/USDT", "price": "50000", "qty": "0.01"},
        "limits": {"allowed_symbols": ["BTC/USDT"], "max_notional_per_order": "1000"},
        "portfolio": {"total_exposure": "100", "daily_loss_quote": "0", "open_orders": 0},
    },
    "backtest_report": {
        "equity_curve": ["10000", "10150", "10100", "10320"],
        "fills": [{"pnl": "150", "notional": "1000"}, {"pnl": "-50", "notional": "1000"}],
    },
    "funding_rate_fetch": {
        "exchange": "bybit",
        "symbol": "BTC/USDT:USDT",
        "from": 0,
        "to": 57_600_000,
    },
    "data_quality_check": {
        "records": [
            {"exchange_ts": 60_000, "close": "50000", "volume": "1.5"},
            {"exchange_ts": 120_000, "close": "50100", "volume": "2.0"},
        ],
        "expected_step_ms": 60_000,
    },
}


def _render_skill_test_file(skill_id: str, sample_payload: dict[str, Any]) -> str:
    """Generate a 6-test `test_skill.py` suite per Appendix A.5 & DoD-39."""
    return (
        '"""Auto-seeded 6-case verification suite for crypto domain skill."""\n\n'
        "from __future__ import annotations\n\n"
        "import importlib.util\n"
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "import pytest\n\n"
        '_SKILL_PATH = Path(__file__).with_name("skill.py")\n'
        f'_SPEC = importlib.util.spec_from_file_location("skill_{skill_id}", _SKILL_PATH)\n'
        "assert _SPEC is not None and _SPEC.loader is not None\n"
        "skill = importlib.util.module_from_spec(_SPEC)\n"
        "_SPEC.loader.exec_module(skill)\n\n"
        f"SAMPLE = {sample_payload!r}\n\n\n"
        "def test_valid_payload_returns_dict() -> None:\n"
        "    res = skill.run(dict(SAMPLE))\n"
        "    assert isinstance(res, dict) and len(res) >= 1\n\n\n"
        "def test_deterministic_double_run() -> None:\n"
        "    assert skill.run(dict(SAMPLE)) == skill.run(dict(SAMPLE))\n\n\n"
        "def test_metadata_contract_complete() -> None:\n"
        f'    assert skill.SKILL_METADATA["id"] == "{skill_id}"\n'
        '    assert "output_schema" in skill.SKILL_METADATA\n\n\n'
        "def test_output_json_serializable() -> None:\n"
        "    dumped = json.dumps(skill.run(dict(SAMPLE)))\n"
        "    assert isinstance(json.loads(dumped), dict)\n\n\n"
        "def test_cli_stdin_stdout_roundtrip() -> None:\n"
        '    script = Path(__file__).with_name("skill.py")\n'
        "    proc = subprocess.run(\n"
        "        [sys.executable, str(script)],\n"
        "        input=json.dumps(SAMPLE),\n"
        "        text=True,\n"
        "        capture_output=True,\n"
        "        check=True,\n"
        "    )\n"
        "    assert isinstance(json.loads(proc.stdout), dict)\n\n\n"
        "def test_empty_or_invalid_payload_handling() -> None:\n"
        f'    if "{skill_id}" == "ws_replay":\n'
        '        assert skill.run({})["events"] == []\n'
        "    else:\n"
        "        with pytest.raises((ValueError, KeyError, TypeError)):\n"
        "            skill.run({})\n"
    )


class CryptoTradingDomainPack:
    """Complete Part VIII Domain Pack: 8 professions, 12 skills, 13 AST rules, 34-node DAG."""

    @property
    def domain_id(self) -> str:
        """Return canonical domain identifier."""
        return "crypto_trading"

    def role_blueprints(self) -> tuple[DomainRoleBlueprint, ...]:
        """Return all 8 default crypto professions (§38.11)."""
        return CRYPTO_ROLE_BLUEPRINTS

    def skill_ids(self) -> tuple[str, ...]:
        """Return all 12 crypto skill IDs (§38.12)."""
        return CRYPTO_SKILL_MODULES

    def mandatory_escalations(self) -> tuple[EscalationQuestionTemplate, ...]:
        """Return all 12 mandatory business escalations (§38.16)."""
        return MANDATORY_CRYPTO_ESCALATIONS

    def seed_talents(self, talents_dir: Path) -> list[str]:
        """Seed 8 professions with `profile.json` and empty `DISCOVERY_DIARY.md` (DP-54..55)."""
        tstore = TalentsStore(talents_dir)
        seeded: list[str] = []
        for bp in CRYPTO_ROLE_BLUEPRINTS:
            if tstore.load(bp.role_id) is None:
                profile = TalentProfile(
                    role_id=bp.role_id,
                    display_name=bp.title,
                    tags=bp.domain_tags,
                    preferred_skills=CRYPTO_SKILL_MODULES[:4],
                    forbidden_libs=bp.forbidden_libs,
                    style_rules=bp.style_rules,
                )
                tstore.save(profile)
            diary_file = tstore.role_dir(bp.role_id) / "DISCOVERY_DIARY.md"
            if not diary_file.exists():
                atomic_write_text(diary_file, "")
            seeded.append(bp.role_id)
        return seeded

    def seed_skills(self, skills_dir: Path, registry: SkillRegistry | None = None) -> list[str]:
        """Seed `skill.yaml`, `skill.py`, and `test_skill.py` for all 12 crypto skills (DoD-39)."""
        skills_dir.mkdir(parents=True, exist_ok=True)
        if registry is not None:
            registry.register_package_modules(
                package_prefix="orchestra.skills.builtin.crypto",
                module_names=CRYPTO_SKILL_MODULES,
            )

        seeded: list[str] = []
        for sid in CRYPTO_SKILL_MODULES:
            mod = importlib.import_module(f"orchestra.skills.builtin.crypto.{sid}")
            meta: dict[str, Any] = mod.SKILL_METADATA
            src = Path(str(mod.__file__)).read_text(encoding="utf-8")
            bundle_dir = skills_dir / sid
            bundle_dir.mkdir(parents=True, exist_ok=True)
            yaml_doc = {
                "skill_id": sid,
                "version": meta["version"],
                "summary": meta["description"],
                "signature": {
                    "input": meta["input_schema"],
                    "output": meta["output_schema"],
                },
                "tags": list(meta["tags"]),
                "status": "ACTIVE",
                "network": False,
            }
            atomic_write_text(
                bundle_dir / "skill.yaml",
                yaml.safe_dump(yaml_doc, sort_keys=False, allow_unicode=True),
            )
            atomic_write_text(bundle_dir / "skill.py", src)
            atomic_write_text(
                bundle_dir / "test_skill.py",
                _render_skill_test_file(sid, _SKILL_SAMPLE_INPUTS[sid]),
            )
            seeded.append(sid)
        return seeded

    def verify_files(self, workspace_root: Path, rel_paths: list[str]) -> list[VerifierFinding]:
        """Run all 13 `crypto_trading` AST checks on `rel_paths` (§38.13, DoD-40)."""
        return verify_crypto_domain_files(workspace_root, rel_paths)

    def verify_workspace(self, workspace_dir: Path) -> list[str]:
        """`DomainVerifierHook` adapter scanning all `.py` files under `workspace_dir`."""
        rel_py = [
            p.relative_to(workspace_dir).as_posix()
            for p in sorted(workspace_dir.rglob("*.py"))
            if not p.name.startswith("test_")
        ]
        findings = self.verify_files(workspace_dir, rel_py)
        return [f"[{f.code}] {f.file_path}:{f.line}: {f.message}" for f in findings]

    def build_reference_map(
        self,
        *,
        project_id: str = "crypto_algo_base",
        project_name: str = "Multi-Exchange Algorithmic Strategy Base",
    ) -> MentalMap:
        """Construct the 34-node canonical `MentalMap` decomposition from §38.15 (DoD-41)."""
        edges = tuple(f"edge_case_{i:02d}" for i in range(1, 11))
        ac = (
            AcceptanceCriterion(id="AC_JUDGE", description="Independent Judge + crypto_ast == 0"),
        )

        contract_defs = {
            "N02": ("C_DOMAIN_TYPES", "class Amount: value: Decimal; asset: str"),
            "N03": ("C_GATEWAY", "class ExchangeGateway(Protocol): ..."),
            "N04": ("C_STRATEGY", "class Strategy(Protocol): def on_bar(...): ..."),
            "N05": ("C_RISK_LIMITS", "class RiskLimits(BaseModel): ..."),
            "N06": ("C_PERSISTENCE", "class AuditLog(Protocol): ..."),
        }

        consumers_by_contract: dict[str, list[str]] = {cid: [] for cid, _ in contract_defs.values()}
        for nid, _slug, _ntype, _role, deps in REFERENCE_CRYPTO_NODES:
            for dep in deps:
                if dep in contract_defs:
                    cid = contract_defs[dep][0]
                    consumers_by_contract[cid].append(nid)

        contracts: dict[str, ContractSignature] = {}
        for owner_nid, (cid, code_sig) in contract_defs.items():
            contracts[cid] = ContractSignature.create(
                contract_id=cid,
                owner_node_id=owner_nid,
                consumer_node_ids=tuple(sorted(consumers_by_contract[cid])),
                definition=code_sig,
                frozen=True,
            )

        nodes: dict[str, MapNode] = {}
        for nid, slug, ntype, role, deps in REFERENCE_CRYPTO_NODES:
            provided = (contract_defs[nid][0],) if nid in contract_defs else ()
            consumed = tuple(contract_defs[d][0] for d in deps if d in contract_defs)
            ext = "md" if ntype == "docs" else "py"
            rel_file = (
                f"tests/{slug.replace('-', '_')}.py"
                if ntype == "test"
                else f"src/{slug.replace('-', '_')}.{ext}"
            )
            desc = (
                f"Crypto node {slug} (DEFAULT_MODE=PAPER; "
                "EDUCATIONAL EXAMPLE — NOT INVESTMENT ADVICE)"
            )
            nodes[nid] = MapNode(
                id=nid,
                title=slug,
                description=desc,
                type=ntype,
                owner_role=role,
                status="READY" if not deps else "PENDING_DEPS",
                dependencies=deps,
                contracts_provided=provided,
                contracts_consumed=consumed,
                owned_files=(rel_file,),
                acceptance_criteria=ac,
                edge_cases=edges,
            )

        return MentalMap(
            project_id=project_id,
            project_name=project_name,
            domain="crypto_trading",
            vision=(
                "Multi-exchange algorithmic strategy base in PAPER mode by default "
                "(EDUCATIONAL EXAMPLE — NOT INVESTMENT ADVICE)."
            ),
            tech_stack={
                "default_trading_mode": "PAPER",
                "exchange_client": "ccxt",
                "money_type": "decimal.Decimal",
                "out_of_scope": "cross_exchange_transfers,p2p_layer",
            },
            nodes=nodes,
            contracts=contracts,
        )
