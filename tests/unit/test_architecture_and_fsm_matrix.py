"""Unit & architectural invariant matrix: FSM (144 pairs), import layers, T-05, T-17, WIN-07..13."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest

from orchestra.comms.rpc_protocol import ContractQueryPayload, ContractReplyPayload, RpcEnvelope
from orchestra.config.constants import PATH_LENGTH_WARN
from orchestra.domains.crypto_ast_verifier import CRYPTO_VERIFIER_RULES, scan_crypto_python_source
from orchestra.errors import CaseCollisionError, IllegalNodeTransition
from orchestra.providers.token_counter import count_tokens_sync
from orchestra.state.event_log import atomic_write_text
from orchestra.state.mental_map import ALLOWED_NODE_TRANSITIONS, MapNode, NodeStatus
from orchestra.verify.patch_engine import CreateFileOp, PatchEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRA_ROOT = REPO_ROOT / "orchestra"

ALL_NODE_STATUSES: tuple[NodeStatus, ...] = get_args(NodeStatus)

FORBIDDEN_FRAMEWORKS: frozenset[str] = frozenset(
    {"langgraph", "langchain", "autogen", "crewai", "llama_index", "openai_agents"}
)

SUBPACKAGE_LAYER: dict[str, int] = {
    "config": 0,
    "security": 0,
    "providers": 1,
    "inference": 1,
    "observability": 2,
    "telemetry": 2,
    "state": 2,
    "skills": 2,
    "research": 2,
    "comms": 2,
    "agents": 3,
    "talents": 3,
    "prompts": 3,
    "verify": 3,
    "domains": 3,
    "swarm": 3,
    "interfaces": 4,
}


@pytest.mark.parametrize("src_status", ALL_NODE_STATUSES)
@pytest.mark.parametrize("dst_status", ALL_NODE_STATUSES)
def test_fsm_exhaustive_transition_matrix(src_status: NodeStatus, dst_status: NodeStatus) -> None:
    """Verify T-07: every (from, to) pair in NodeStatus obeys ALLOWED_NODE_TRANSITIONS."""
    node = MapNode(id="N1", title="FSM Node", status=src_status)
    allowed = (src_status == dst_status) or (dst_status in ALLOWED_NODE_TRANSITIONS[src_status])
    if allowed:
        moved = node.transition_to(dst_status)
        assert moved.status == dst_status
    else:
        with pytest.raises(IllegalNodeTransition):
            node.transition_to(dst_status)


def _runtime_orchestra_imports(py_path: Path) -> set[str]:
    """Collect runtime (outside `if TYPE_CHECKING:`) top-level `orchestra.<pkg>` imports."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    found: set[str] = set()

    def _visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if (
                isinstance(stmt, ast.If)
                and isinstance(stmt.test, ast.Name)
                and stmt.test.id == "TYPE_CHECKING"
            ):
                continue
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_FRAMEWORKS
                    if root == "orchestra" and len(alias.name.split(".")) >= 2:
                        found.add(alias.name.split(".")[1])
            elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                root = stmt.module.split(".")[0]
                assert root not in FORBIDDEN_FRAMEWORKS
                if root == "orchestra" and len(stmt.module.split(".")) >= 2:
                    found.add(stmt.module.split(".")[1])
            for child_attr in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, child_attr, None)
                if isinstance(sub, list):
                    _visit(sub)

    _visit(tree.body)
    return found


def test_import_graph_acyclic_layered_and_file_line_caps() -> None:
    """Verify T-13, DoD-09, DoD-10, QS-10: zero import cycles, layer bounds, <=500 lines/file."""
    pkg_edges: dict[str, set[str]] = {pkg: set() for pkg in SUBPACKAGE_LAYER}
    for py_file in sorted(ORCHESTRA_ROOT.rglob("*.py")):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 500, f"{py_file} exceeds 500-line limit ({len(lines)} lines)"
        rel_parts = py_file.relative_to(ORCHESTRA_ROOT).parts
        if len(rel_parts) < 2:
            continue
        owner_pkg = rel_parts[0]
        if owner_pkg not in SUBPACKAGE_LAYER:
            continue
        for target_pkg in _runtime_orchestra_imports(py_file):
            if target_pkg in SUBPACKAGE_LAYER and target_pkg != owner_pkg:
                assert SUBPACKAGE_LAYER[target_pkg] <= SUBPACKAGE_LAYER[owner_pkg] or (
                    SUBPACKAGE_LAYER[owner_pkg] == 3 and SUBPACKAGE_LAYER[target_pkg] == 3
                ), f"Upward layer import {owner_pkg} -> {target_pkg} in {py_file.name}"
                pkg_edges[owner_pkg].add(target_pkg)

    for low_pkg in ("config", "security", "providers", "state"):
        assert "interfaces" not in pkg_edges[low_pkg]
        assert "swarm" not in pkg_edges[low_pkg]


def test_canonical_rpc_pair_fits_under_120_tokens() -> None:
    """Verify T-05 (M-07): canonical CONTRACT_QUERY + CONTRACT_REPLY <= 120 tokens."""
    q_payload = ContractQueryPayload(contract_id="C_AUTH", question_code="CHECK_SIG")
    q = RpcEnvelope(
        sender_id="dev_01",
        recipient_id="dev_02",
        node_id="N1",
        msg_type="CONTRACT_QUERY",
        note="CHECK RETURN TUPLE",
        payload=q_payload.model_dump(mode="json"),
    )
    r_payload = ContractReplyPayload(
        contract_id="C_AUTH",
        version=1,
        sha256="a" * 64,
        summary="FROZEN SIG VERIFY_JWT",
    )
    r = RpcEnvelope(
        sender_id="dev_02",
        recipient_id="dev_01",
        node_id="N1",
        correlation_id=q.msg_id,
        msg_type="CONTRACT_REPLY",
        note="CONFIRMED FROZEN",
        payload=r_payload.model_dump(mode="json"),
    )
    compact_wire = (
        f"{q.msg_type}|{q.sender_id}->{q.recipient_id}|{q_payload.model_dump_json()}\n"
        f"{r.msg_type}|{r.sender_id}->{r.recipient_id}|{r_payload.model_dump_json()}"
    )
    assert count_tokens_sync(compact_wire) <= 120


_CRYPTO_RULE_CASES: dict[str, tuple[str, str]] = {
    "NO_FLOAT_MONEY": (
        "price: float = 1.5\n",
        "from decimal import Decimal\nprice = Decimal('1.5')\n",
    ),
    "NO_NAIVE_DATETIME": (
        "from datetime import datetime\nx = datetime.utcnow()\n",
        "from datetime import UTC, datetime\nx = datetime.now(UTC)\n",
    ),
    "NO_WALLCLOCK_IN_STRATEGY": (
        "import time\nx = time.time()\n",
        "def on_bar(event_ts: int) -> int:\n    return event_ts\n",
    ),
    "NO_DIRECT_EXCHANGE_IMPORT": ("import ccxt\n", "from decimal import Decimal\n"),
    "NO_LOOKAHEAD": ("x = df.shift(-1)\n", "x = feed.get_bars(as_of=100)\n"),
    "MANDATORY_CLIENT_ORDER_ID": (
        "audit = True\ngw.submit_order(symbol='BTC/USDT')\n",
        "audit = True\ngw.submit_order(symbol='BTC/USDT', client_order_id='id1')\n",
    ),
    "ORDER_AUDIT_REQUIRED": (
        "gw.submit_order(symbol='BTC/USDT', client_order_id='id1')\n",
        "audit_log = []\ngw.submit_order(symbol='BTC/USDT', client_order_id='id1')\n",
    ),
    "NO_UNBOUNDED_AVERAGING": (
        "martingale_factor = 2\n",
        "risk_limit = 100\nmartingale_factor = 1\n",
    ),
    "DECIMAL_JSON_SAFETY": (
        "import json\nx = json.dumps({'a': 1}, default=float)\n",
        "import json\nx = json.dumps({'a': '1.00'})\n",
    ),
    "SECRET_NOT_LITERAL": (
        "api_secret = '1234567890abcdef'\n",
        "import os\napi_secret = os.getenv('REF')\n",
    ),
    "LIVE_MODE_GUARD": (
        "m = TradingMode.LIVE\n",
        "human_confirmed_live = True\nm = TradingMode.LIVE\n",
    ),
    "NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER": (
        "H = 'X-MBX-USED-WEIGHT-1M'\n",
        "H = 'NORMALIZED_HEADER'\n",
    ),
    "NO_FLOAT_CROSS_EXCHANGE": (
        "cross_exchange_pnl: float = 0.0\n",
        "from decimal import Decimal\ncross_exchange_pnl = Decimal('0')\n",
    ),
}


@pytest.mark.parametrize("rule_id", CRYPTO_VERIFIER_RULES)
def test_t17_each_crypto_rule_positive_and_negative(rule_id: str) -> None:
    """Verify T-17 & DoD-40: dedicated positive + negative case for every rule in §38.13."""
    bad_code, good_code = _CRYPTO_RULE_CASES[rule_id]
    bad_hits = {
        f.code for f in scan_crypto_python_source(bad_code, rel_path="src/strategies/mod.py")
    }
    good_hits = {
        f.code for f in scan_crypto_python_source(good_code, rel_path="src/strategies/mod.py")
    }
    assert rule_id in bad_hits
    assert rule_id not in good_hits


def test_windows_portability_case_collision_atomic_retry_and_gitattributes(tmp_path: Path) -> None:
    """Verify DoD-47 (WIN-07..13): CaseCollisionError, PermissionError retry, LF, .gitattributes."""
    gitattr = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=lf" in gitattr
    assert PATH_LENGTH_WARN == 240

    target = tmp_path / "locked.json"
    calls = 0
    orig_replace = Path.replace

    def _flaky_replace(self_path: Path, dst: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("Simulated Windows AV file lock")
        return orig_replace(self_path, dst)

    with patch.object(Path, "replace", _flaky_replace):
        atomic_write_text(target, "line1\r\nline2\n")
    assert calls >= 2
    assert b"\r\n" not in target.read_bytes()

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "core.py").write_text("X = 1\n", encoding="utf-8")
    pe = PatchEngine(ws)
    with pytest.raises(CaseCollisionError):
        pe.apply_transaction(
            [CreateFileOp(path="src/Core.py", content="X = 2\n")],
            allowed_owned_files={"src/core.py", "src/Core.py"},
        )


def test_all_builtin_and_crypto_skills_in_process_execution(tmp_path: Path) -> None:
    """Exercise all 10 core + 12 crypto built-in skills in-process for deterministic coverage."""
    import importlib

    from orchestra.domains.crypto_trading import _SKILL_SAMPLE_INPUTS, CRYPTO_SKILL_MODULES
    from orchestra.skills.builtin import (
        api_signature_diff,
        ast_outline,
        complexity_metrics,
        contract_compat_check,
        dead_code_finder,
        dependency_graph,
        import_verifier,
        json_schema_validator,
        secret_leak_scan,
        test_coverage_gap,
    )

    sample_src = "import math\n\ndef calc(x: int) -> int:\n    return x + 1\n"
    assert ast_outline.run({"source": sample_src})["line_count"] == 4
    assert complexity_metrics.run({"source": sample_src})["max_complexity"] >= 1
    assert isinstance(dead_code_finder.run({"source": sample_src}), dict)
    assert import_verifier.run({"source": sample_src})["valid"] is True
    assert secret_leak_scan.run({"text": "clean=1"})["clean"] is True
    assert isinstance(
        api_signature_diff.run(
            {
                "before_source": "def f(a: int) -> int: ...",
                "after_source": "def f(a: int) -> int: ...",
            }
        ),
        dict,
    )
    assert isinstance(
        contract_compat_check.run(
            {
                "old_code": "def f(a: int) -> int: ...",
                "new_code": "def f(a: int) -> int: return a",
            }
        ),
        dict,
    )
    assert (
        json_schema_validator.run(
            {
                "instance": {"k": "v"},
                "schema": {
                    "type": "object",
                    "required": ["k"],
                    "properties": {"k": {"type": "string"}},
                },
            }
        )["valid"]
        is True
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(sample_src, encoding="utf-8")
    assert isinstance(dependency_graph.run({"root_dir": str(tmp_path / "pkg")}), dict)
    assert isinstance(
        test_coverage_gap.run(
            {"target_source": sample_src, "test_source": "def test_calc() -> None: calc(1)\n"}
        ),
        dict,
    )

    for sid in CRYPTO_SKILL_MODULES:
        mod = importlib.import_module(f"orchestra.skills.builtin.crypto.{sid}")
        out = mod.run(dict(_SKILL_SAMPLE_INPUTS[sid]))
        assert isinstance(out, dict)

    # Exercise all 10 §13.9.3 core skills, DeadlockDetector, synthesis dedup, and TG keyboards
    from orchestra.interfaces.telegram_keyboards import (
        build_sitrep_keyboard,
        build_team_approval_keyboard,
    )
    from orchestra.skills.builtin import (
        append_to_diary,
        apply_patch,
        count_tokens,
        escalate_to_architect,
        git_commit,
        list_dir_tree,
        read_project_ast,
        run_sandbox_test,
        search_documentation,
        verify_api_exists,
    )
    from orchestra.skills.registry import SkillRegistry
    from orchestra.skills.synthesis import check_skill_proposal_uniqueness
    from orchestra.state.mental_map import MentalMap
    from orchestra.swarm.deadlock import DeadlockDetector
    from orchestra.swarm.org_design import OrgDesigner
    from orchestra.telemetry import logging_setup, metrics, resilience

    assert logging_setup is not None and metrics is not None and resilience is not None
    assert OrgDesigner is not None
    assert len(search_documentation.run({"query": "Decimal"})["snippets"]) >= 1
    assert "calc" in read_project_ast.run({"source": sample_src})["symbols"]
    assert (
        run_sandbox_test.run({"cmd": ["python", "-c", "print('ok')"], "workdir": str(tmp_path)})[
            "exit_code"
        ]
        == 0
    )
    ws_p = tmp_path / "patch_ws"
    ws_p.mkdir()
    assert (
        apply_patch.run(
            {
                "workspace": str(ws_p),
                "path": "hello.py",
                "replace": "A = 1\n",
                "mode": "CREATE_FILE",
            }
        )["applied"]
        is True
    )
    assert (
        apply_patch.run(
            {
                "workspace": str(ws_p),
                "path": "hello.py",
                "search": "A = 1",
                "replace": "A = 2",
                "mode": "SEARCH_REPLACE",
            }
        )["applied"]
        is True
    )
    assert (
        append_to_diary.run(
            {
                "talents_dir": str(tmp_path / "tal"),
                "entry": {
                    "role_id": "developer",
                    "project_id": "p1",
                    "status": "HYPOTHESIS",
                    "observation": "obs",
                    "lesson": "lesson",
                },
            }
        )["status"]
        == "HYPOTHESIS"
    )
    assert (
        escalate_to_architect.run(
            {"question": "Pick mode?", "options": ["PAPER", "SHADOW"], "node_id": "N1"}
        )["options_count"]
        == 2
    )
    assert (
        len(git_commit.run({"workspace": str(ws_p), "scope": "core", "message": "init"})["sha"])
        >= 7
    )
    assert verify_api_exists.run({"package": "decimal", "symbol": "Decimal"})["exists"] is True
    assert (
        verify_api_exists.run({"package": "decimal", "symbol": "NoSuchThing99"})["exists"] is False
    )
    assert count_tokens.run({"text": "hello world"})["tokens"] >= 2
    assert "hello.py" in list_dir_tree.run({"path": str(ws_p), "depth": 2})["tree"]

    sreg = SkillRegistry(tmp_path / "sreg", load_builtins=True)
    dup = check_skill_proposal_uniqueness(
        sreg,
        proposed_description="Count tokens in text using ORCHESTRA's offline-safe TokenCounter.",
    )
    assert dup.approved is False and dup.duplicate_of == "count_tokens"

    kb = build_sitrep_keyboard("question_123456", ("Вариант А", "Вариант Б"))
    assert len(kb) == 2 and len(build_team_approval_keyboard("proj_1")) == 1

    dd = DeadlockDetector()
    d_map = MentalMap(
        project_id="p_dead",
        project_name="Dead",
        nodes={"N1": MapNode(id="N1", title="Stuck", status="PENDING_DEPS")},
    )
    assert dd.inspect(d_map).deadlocked is True


def test_dod01_dod04_dod10_dod34_repository_completeness_and_zero_placeholders() -> None:
    """Verify DoD-01 (§10 tree), DoD-04 (.gitignore), DoD-10 (0 TODOs), and DoD-34 (ADRs)."""
    required_paths = (
        "ARCHITECTURE.md",
        "README.md",
        "CHANGELOG.md",
        "Makefile",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "exchanges.yaml",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        ".dockerignore",
        "Dockerfile",
        "docker-compose.yml",
        "ruff.toml",
        "mypy.ini",
        "pytest.ini",
        "docs/RUNBOOK.md",
        "docs/PROTOCOLS.md",
        "docs/DEFAULTS.md",
        "docs/PROGRESS.md",
        "docs/DOMAIN_CRYPTO_TRADING.md",
        "orchestra/swarm/org_design.py",
        "orchestra/swarm/deadlock.py",
        "orchestra/skills/synthesis.py",
        "orchestra/interfaces/telegram_keyboards.py",
        "orchestra/web/index.html",
        "orchestra/web/app.js",
        "orchestra/web/styles.css",
        "orchestra/telemetry/logging_setup.py",
        "orchestra/telemetry/metrics.py",
        "orchestra/telemetry/resilience.py",
        "scripts/bootstrap_demo.py",
        "scripts/check_secrets_leak.py",
    )
    for rel in required_paths:
        assert (REPO_ROOT / rel).exists(), f"Missing §10 required file: {rel}"

    adrs = list((REPO_ROOT / "docs" / "adr").glob("ADR-*.md"))
    assert len(adrs) >= 8
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/" in gitignore

    todo_marker = "TO" + "DO"
    fixme_marker = "FIX" + "ME"
    for py_file in ORCHESTRA_ROOT.rglob("*.py"):
        if py_file.name in {"verifiers.py"}:
            continue
        text = py_file.read_text(encoding="utf-8")
        assert todo_marker not in text, f"Placeholder {todo_marker} found in {py_file}"
        assert fixme_marker not in text, f"Placeholder {fixme_marker} found in {py_file}"
        assert "raise NotImplementedError" not in text, f"NotImplementedError in {py_file}"
