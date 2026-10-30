"""Unit tests for Phase P7: ExecutionSandbox, PatchEngine, GitOps, Verifiers, and Judge."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from orchestra.agents.agent_runtime import AgentExecutionResult
from orchestra.agents.roles_catalog import instantiate_charter
from orchestra.errors import (
    AmbiguousPatch,
    CaseCollisionError,
    DisallowedCommand,
    FileOwnershipViolation,
    HallucinatedApiError,
    PatchSyntaxError,
    SandboxTimeoutError,
    SecurityViolation,
    StaleContextHashError,
)
from orchestra.state.mental_map import MapNode
from orchestra.verify.git_ops import GitOperationError, GitOps
from orchestra.verify.judge import IndependentJudge
from orchestra.verify.patch_engine import (
    CreateFileOp,
    LineRangeOp,
    PatchEngine,
    SearchReplaceBlock,
    SearchReplaceOp,
    compute_lines_sha256,
)
from orchestra.verify.sandbox import ExecutionSandbox, resolve_safe_workspace_path
from orchestra.verify.verifiers import (
    run_spot_mutation_check,
    scan_placeholders_and_skipped_tests,
    verify_grounded_imports,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_execution_sandbox_whitelist_path_traversal_and_timeout_tree_kill(
    tmp_path: Path,
) -> None:
    """Verify sandbox blocks disallowed binaries, path traversal, and kills timed-out trees."""
    with pytest.raises(SecurityViolation):
        resolve_safe_workspace_path(tmp_path, "../../etc/passwd")
    with pytest.raises(SecurityViolation):
        resolve_safe_workspace_path(tmp_path, "/etc/passwd")

    sb = ExecutionSandbox(tmp_path, timeout_s=5.0)
    with pytest.raises(DisallowedCommand):
        await sb.run(("bash", "-c", "echo nope"))

    ok_res = await sb.run((sys.executable, "-c", "print('sandbox_ok')"))
    assert ok_res.exit_code == 0
    assert "sandbox_ok" in ok_res.stdout

    with pytest.raises(SandboxTimeoutError):
        await sb.run((sys.executable, "-c", "import time; time.sleep(10)"), timeout_s=0.2)


def test_patch_engine_all_3_formats_rollback_ownership_and_case_collision(
    tmp_path: Path,
) -> None:
    """Verify CREATE_FILE, SEARCH_REPLACE, LINE_RANGE, AST rollback, ownership, and case check."""
    engine = PatchEngine(tmp_path)

    # 1) CREATE_FILE
    recs = engine.apply_transaction(
        [
            CreateFileOp(
                path="pkg/calc.py",
                content="def add(a: int, b: int) -> int:\n    return a + b\n",
            )
        ],
        allowed_owned_files={"pkg/calc.py"},
    )
    assert len(recs) == 1
    assert (tmp_path / "pkg" / "calc.py").is_file()

    # Ownership violation rejected
    with pytest.raises(FileOwnershipViolation):
        engine.apply_transaction(
            [CreateFileOp(path="pkg/other.py", content="x = 1\n")],
            allowed_owned_files={"pkg/calc.py"},
        )

    # Case collision rejected (WIN-10)
    with pytest.raises(CaseCollisionError):
        engine.apply_transaction([CreateFileOp(path="pkg/CALC.py", content="x = 1\n")])

    # 2) SEARCH_REPLACE: 0 matches -> AmbiguousPatch; exact 1 match -> applied
    with pytest.raises(AmbiguousPatch):
        engine.apply_transaction(
            [
                SearchReplaceOp(
                    path="pkg/calc.py",
                    blocks=(SearchReplaceBlock(search="missing_token", replace="x"),),
                )
            ]
        )

    engine.apply_transaction(
        [
            SearchReplaceOp(
                path="pkg/calc.py",
                blocks=(SearchReplaceBlock(search="return a + b", replace="return a + b + 0"),),
            )
        ]
    )
    assert "a + b + 0" in (tmp_path / "pkg" / "calc.py").read_text(encoding="utf-8")

    # 3) LINE_RANGE with SHA-256 verification
    lines = (tmp_path / "pkg" / "calc.py").read_text(encoding="utf-8").splitlines()
    h = compute_lines_sha256(lines[1:2])
    with pytest.raises(StaleContextHashError):
        engine.apply_transaction(
            [
                LineRangeOp(
                    path="pkg/calc.py",
                    start_line=2,
                    end_line=2,
                    expected_sha256="wrong_hash",
                    replacement="    return a + b",
                )
            ]
        )

    engine.apply_transaction(
        [
            LineRangeOp(
                path="pkg/calc.py",
                start_line=2,
                end_line=2,
                expected_sha256=h,
                replacement="    return a + b",
            )
        ]
    )

    # Syntax error in second op must raise PatchSyntaxError without corrupting first file!
    before_text = (tmp_path / "pkg" / "calc.py").read_text(encoding="utf-8")
    with pytest.raises(PatchSyntaxError):
        engine.apply_transaction(
            [
                SearchReplaceOp(
                    path="pkg/calc.py",
                    blocks=(SearchReplaceBlock(search="return a + b", replace="return a - b"),),
                ),
                CreateFileOp(path="pkg/broken.py", content="def bad(:\n"),
            ]
        )
    assert (tmp_path / "pkg" / "calc.py").read_text(encoding="utf-8") == before_text
    assert not (tmp_path / "pkg" / "broken.py").exists()


async def test_git_ops_verifiers_spot_mutation_and_independent_judge(tmp_path: Path) -> None:
    """Verify GitOps branch guard, BAN-01/02 scanner, spot mutation, and IndependentJudge."""
    ws = tmp_path / "workspace"
    git = GitOps(ws, forbid_direct_main_commits=True)
    await git.init_workspace_repo(working_branch="orchestra/feat-1")

    engine = PatchEngine(ws)
    engine.apply_transaction(
        [
            CreateFileOp(
                path="math_mod.py",
                content=(
                    '"""Simple math module."""\n\n'
                    "def is_positive(val: int) -> bool:\n"
                    "    return val >= 1\n"
                ),
            ),
            CreateFileOp(
                path="test_math_mod.py",
                content=(
                    '"""Tests for math_mod."""\n\n'
                    "from math_mod import is_positive\n\n\n"
                    "def test_is_positive() -> None:\n"
                    "    assert is_positive(5) is True\n"
                    "    assert is_positive(0) is False\n"
                ),
            ),
        ]
    )
    sha = await git.commit_all("feat: initial verified math_mod")
    assert len(sha) >= 7

    await git.checkout_branch("main", create=True)
    with pytest.raises(GitOperationError, match="protected branch"):
        await git.commit_all("should fail on main")
    await git.checkout_branch("orchestra/feat-1")

    # BAN-01 & BAN-02 & RS-08 detection on scratch file
    bad_file = tmp_path / "bad_stub.py"
    bad_file.write_text(
        "# TODO: finish later\n"
        "import fake_missing_pkg_123\n"
        "def f():\n    raise NotImplementedError()\n",
        encoding="utf-8",
    )
    stub_defects = scan_placeholders_and_skipped_tests(bad_file)
    assert len(stub_defects) >= 2
    with pytest.raises(HallucinatedApiError):
        verify_grounded_imports(bad_file)

    # Spot mutation check kills mutant on math_mod.py
    killed, _ = await run_spot_mutation_check(ws, "math_mod.py")
    assert killed is True

    # IndependentJudge full evaluation produces PASS + EmpiricalProof!
    judge_impl = IndependentJudge(ws, mutation_probability=1.0)
    judge_charter = instantiate_charter("independent_judge", instance_index=1)
    node = MapNode(
        id="N1",
        title="Math Mod",
        status="VERIFYING",
        owned_files=("math_mod.py", "test_math_mod.py"),
    )
    exec_res = AgentExecutionResult(
        agent_id="core_developer_01",
        role_id="core_developer",
        node_id="N1",
        content_text="implemented math_mod",
        tokens_used=200,
        proposed_payload={},
    )
    evaluation, proof = await judge_impl.evaluate_node(node, exec_res, judge_charter)
    assert evaluation.passed is True, f"Unexpected defects: {evaluation.defects}"
    assert proof is not None and proof.verified_by_judge is True
