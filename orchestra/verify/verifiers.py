"""Deterministic code verifiers: AST ban scanner, imports, secrets, tools, and spot mutation."""

from __future__ import annotations

import ast
import re
import shutil
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from orchestra.errors import HallucinatedApiError
from orchestra.skills.builtin import import_verifier, secret_leak_scan
from orchestra.state.contracts import ContractSignature, compute_contract_hash
from orchestra.verify.sandbox import ExecutionSandbox

BANNED_COMMENT_MARKERS: re.Pattern[str] = re.compile(
    r"#.*\b(TODO|FIXME|XXX|HACK)\b",
    re.IGNORECASE,
)

SKIPPED_TEST_MARKERS: re.Pattern[str] = re.compile(
    r"(@pytest\.mark\.skip\b|pytest\.skip\s*\(|@unittest\.skip\b)",
)


class VerifierFinding(BaseModel):
    """Normalized static/AST verifier finding across core and domain verifiers (§SB-15, DoD-19)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verifier: str
    file_path: str
    line: int
    code: str
    message: str
    severity: str = "ERROR"


class VerifierSuiteReport(BaseModel):
    """Aggregated deterministic verification report for a workspace or node (§13.12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    defects: tuple[str, ...]
    commands_run: tuple[str, ...] = ()


def scan_placeholders_and_skipped_tests(
    file_path: Path,
    *,
    is_test_file: bool = False,
    max_file_lines: int = 500,
) -> list[str]:
    """Enforce BAN-01 (no TODO/FIXME/NotImplementedError/pass) and BAN-02 (no skipped tests)."""
    source = file_path.read_text(encoding="utf-8")
    defects: list[str] = []
    lines = source.splitlines()
    if len(lines) > max_file_lines:
        defects.append(f"{file_path.name}: {len(lines)} lines exceeds QS-10 cap {max_file_lines}")

    for lineno, line in enumerate(lines, start=1):
        if BANNED_COMMENT_MARKERS.search(line):
            defects.append(f"{file_path.name}:{lineno}: banned TODO/FIXME/HACK marker (BAN-01)")
        if is_test_file and SKIPPED_TEST_MARKERS.search(line):
            defects.append(f"{file_path.name}:{lineno}: skipped test forbidden (BAN-02)")

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        defects.append(f"{file_path.name}:{exc.lineno}: SyntaxError: {exc.msg}")
        return defects

    _inspect_ast_bodies(tree, file_name=file_path.name, defects=defects)
    return defects


def _inspect_ast_bodies(tree: ast.Module, *, file_name: str, defects: list[str]) -> None:
    protocol_or_abc_classes: set[ast.ClassDef] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases
            }
            if base_names & {"Protocol", "ABC"}:
                protocol_or_abc_classes.add(node)

    exempt_fns: set[ast.AST] = set()
    for cls_node in protocol_or_abc_classes:
        for item in cls_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                exempt_fns.add(item)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, (ast.Call, ast.Name)):
            exc_name = (
                node.exc.id if isinstance(node.exc, ast.Name) else getattr(node.exc.func, "id", "")
            )
            if exc_name == "NotImplementedError":
                defects.append(
                    f"{file_name}:{node.lineno}: NotImplementedError stub forbidden (BAN-01)"
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in exempt_fns:
            has_abstract_dec = any(
                (isinstance(d, ast.Name) and "abstract" in d.id)
                or (isinstance(d, ast.Attribute) and "abstract" in d.attr)
                for d in node.decorator_list
            )
            if has_abstract_dec:
                continue
            stmts = list(node.body)
            # Strip leading docstring
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            if len(stmts) == 1 and isinstance(stmts[0], ast.Pass):
                # Allow empty exception classes or explicit no-op hooks starting with on_ / close
                if node.name not in ("__init__", "close"):
                    defects.append(
                        f"{file_name}:{node.lineno}: empty 'pass' stub in '{node.name}' (BAN-01)"
                    )
            elif (
                len(stmts) == 1
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and stmts[0].value.value is Ellipsis
            ):
                defects.append(
                    f"{file_name}:{node.lineno}: ellipsis '...' stub in '{node.name}' (BAN-01)"
                )


def verify_grounded_imports(file_path: Path, *, workspace_root: Path | None = None) -> None:
    """Verify external imports exist in installed environment; raise HallucinatedApiError."""
    roots = [str(workspace_root or file_path.parent)]
    res = import_verifier.run(
        {"source": file_path.read_text(encoding="utf-8"), "search_roots": roots}
    )
    if not res["valid"]:
        raise HallucinatedApiError(
            f"Hallucinated imports in {file_path.name}: "
            f"modules={res['missing_modules']}, symbols={res['missing_symbols']}"
        )


def verify_frozen_contracts_intact(
    contracts: list[ContractSignature] | tuple[ContractSignature, ...],
) -> list[str]:
    """Verify every frozen contract's definition still matches its recorded SHA-256."""
    defects: list[str] = []
    for contract in contracts:
        if contract.frozen:
            actual = compute_contract_hash(contract.definition)
            if actual != contract.sha256:
                defects.append(
                    f"Frozen contract '{contract.id}' hash drift: expected {contract.sha256[:12]}, "
                    f"got {actual[:12]}"
                )
    return defects


async def run_sandbox_tool_verifiers(
    sandbox: ExecutionSandbox,
    *,
    run_ruff: bool = True,
    run_mypy: bool = True,
    run_pytest: bool = True,
) -> VerifierSuiteReport:
    """Run ruff, mypy --strict, pytest, placeholder scan, and secret scan in `sandbox`."""
    defects: list[str] = []
    commands_run: list[str] = []

    py_files = [
        p
        for p in sorted(sandbox.workspace_dir.rglob("*.py"))
        if not any(part.startswith(".") or part == "__pycache__" for part in p.parts)
    ]
    for pyf in py_files:
        is_test = "test" in pyf.name or "tests" in pyf.parts
        defects.extend(scan_placeholders_and_skipped_tests(pyf, is_test_file=is_test))

    leak_res = secret_leak_scan.run({"target_path": str(sandbox.workspace_dir)})
    if not leak_res["clean"]:
        defects.append(f"Secret leak detected: {leak_res['findings']}")

    py_exe = sys.executable
    steps: list[tuple[str, tuple[str, ...]]] = []
    if run_ruff and py_files:
        steps.append(("ruff", (py_exe, "-m", "ruff", "check", ".")))
    if run_mypy and py_files:
        steps.append(("mypy", (py_exe, "-m", "mypy", "--strict", ".")))
    if run_pytest and any("test" in p.name for p in py_files):
        steps.append(("pytest", (py_exe, "-m", "pytest", "-q")))

    for label, cmd in steps:
        commands_run.append(" ".join(cmd))
        res = await sandbox.run(cmd)
        if res.exit_code != 0:
            snippet = res.tail_error.strip().replace("\n", " | ")[:350]
            defects.append(f"{label} failed (exit={res.exit_code}): {snippet}")

    return VerifierSuiteReport(
        passed=len(defects) == 0,
        defects=tuple(defects),
        commands_run=tuple(commands_run),
    )


async def run_spot_mutation_check(
    workspace_dir: Path,
    target_rel_file: str,
) -> tuple[bool, str]:
    """Mutate one comparison/boolean/math operator in a temp copy and require pytest to fail."""
    with tempfile.TemporaryDirectory(prefix="orch_mut_") as tmp:
        copy_dir = Path(tmp) / "ws"
        shutil.copytree(workspace_dir, copy_dir)
        target = copy_dir / target_rel_file
        if not target.is_file():
            return (True, "Target file absent; skipped mutation")

        orig = target.read_text(encoding="utf-8")
        mutated = _apply_single_mutation(orig)
        if mutated is None:
            return (True, "No mutable operator in target file")

        target.write_text(mutated, encoding="utf-8", newline="\n")
        sb = ExecutionSandbox(copy_dir, timeout_s=30.0)
        res = await sb.run((sys.executable, "-m", "pytest", "-q"))
        if res.exit_code == 0:
            return (
                False,
                f"Survived mutant in '{target_rel_file}': tests passed despite operator flip!",
            )
        return (True, "Mutant killed by pytest")


def _apply_single_mutation(source: str) -> str | None:
    swaps = (
        (" == ", " != "),
        (" != ", " == "),
        (" >= ", " < "),
        (" <= ", " > "),
        ("return True", "return False"),
        ("return False", "return True"),
        (" + ", " - "),
    )
    for old_tok, new_tok in swaps:
        if old_tok in source:
            return source.replace(old_tok, new_tok, 1)
    return None
