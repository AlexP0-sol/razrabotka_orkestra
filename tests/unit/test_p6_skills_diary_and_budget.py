"""Unit tests for Phase P6: Skills Registry, 10 Builtins, Mutation Gate, Diary, and Budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestra.agents.context_budget import ContextBudgetAllocator
from orchestra.agents.roles_catalog import instantiate_charter
from orchestra.errors import DiaryGateViolation, SkillMutationRejected, SkillSecurityError
from orchestra.skills.executor import SkillExecutor
from orchestra.skills.mutation_gate import SkillMutationGate, SkillTestVector
from orchestra.skills.registry import BUILTIN_SKILL_MODULES, SkillRegistry
from orchestra.skills.security_scan import scan_skill_source
from orchestra.skills.synthesizer import SkillSynthesizer
from orchestra.state.mental_map import MapNode, MentalMap
from orchestra.talents.diary_manager import DiaryEntry, DiaryManager, EmpiricalProof
from orchestra.talents.post_mortem import PostMortemAnalyzer
from orchestra.talents.talents_store import TalentsStore

if TYPE_CHECKING:
    from pathlib import Path


async def test_all_10_builtin_skills_execute_in_isolated_subprocesses(tmp_path: Path) -> None:
    """Verify all 10 built-in system skills register and execute cleanly via SkillExecutor."""
    reg = SkillRegistry(tmp_path / "skills", load_builtins=True)
    assert set(BUILTIN_SKILL_MODULES) <= set(reg.manifest.skills.keys())
    executor = SkillExecutor(working_dir=tmp_path)

    # 1) ast_outline
    outline = await reg.invoke(
        "ast_outline",
        {"source": "import math\nclass Box:\n    def area(self) -> int: return 1\n"},
        executor=executor,
    )
    assert outline["classes"][0]["name"] == "Box"

    # 2) dependency_graph
    pkg = tmp_path / "sample_pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import sample_pkg.b\n", encoding="utf-8")
    (pkg / "b.py").write_text("x = 1\n", encoding="utf-8")
    dep = await reg.invoke(
        "dependency_graph",
        {"root_dir": str(pkg), "package_prefix": "sample_pkg"},
        executor=executor,
    )
    assert dep["cycles"] == []

    # 3) contract_compat_check
    compat = await reg.invoke(
        "contract_compat_check",
        {
            "old_code": "def calc(a: int) -> int: return a",
            "new_code": "def calc(a: int, b: int) -> int: return a + b",
        },
        executor=executor,
    )
    assert compat["compatible"] is False

    # 4) json_schema_validator
    jval = await reg.invoke(
        "json_schema_validator",
        {
            "instance": {"price": 10},
            "schema": {
                "type": "object",
                "required": ["price"],
                "properties": {"price": {"type": "integer", "minimum": 1}},
            },
        },
        executor=executor,
    )
    assert jval["valid"] is True

    # 5) complexity_metrics
    comp = await reg.invoke(
        "complexity_metrics",
        {"source": "def f(x: int) -> int:\n    return x + 1 if x > 0 else 0\n"},
        executor=executor,
    )
    assert comp["max_complexity"] >= 2

    # 6) test_coverage_gap
    gap = await reg.invoke(
        "test_coverage_gap",
        {
            "target_source": "def covered(): return 1\ndef uncovered(): return 2\n",
            "test_source": "def test_it(): assert covered() == 1\n",
        },
        executor=executor,
    )
    assert gap["unreferenced_symbols"] == ["uncovered"]

    # 7) secret_leak_scan
    fake_secret = "nvapi-" + "A9b8C7d6E5f4G3h2J1k0L9m8N7"
    scan = await reg.invoke("secret_leak_scan", {"text": fake_secret}, executor=executor)
    assert scan["clean"] is False

    # 8) dead_code_finder
    dead = await reg.invoke(
        "dead_code_finder",
        {"source": "def _unused(): return 1\ndef pub():\n    return 2\n    x = 3\n"},
        executor=executor,
    )
    assert "_unused" in dead["unused_private"]
    assert 4 in dead["unreachable_lines"]

    # 9) api_signature_diff
    diff = await reg.invoke(
        "api_signature_diff",
        {
            "before_source": "def alpha(x): return x\n",
            "after_source": "def alpha(x, y): return x\ndef beta(): return 1\n",
        },
        executor=executor,
    )
    assert diff["added"] == ["beta"]
    assert diff["modified"] == ["alpha"]

    # 10) import_verifier
    imp = await reg.invoke(
        "import_verifier",
        {"source": "from math import sqrt, nonexistent_fn_xyz\n"},
        executor=executor,
    )
    assert imp["valid"] is False
    assert "math.nonexistent_fn_xyz" in imp["missing_symbols"]


async def test_skill_security_scan_and_mutation_gate(tmp_path: Path) -> None:
    """Verify INV-06: SkillMutationGate blocks unsafe code, breaking schemas, and failing tests."""
    with pytest.raises(SkillSecurityError):
        scan_skill_source("import socket\n", skill_id="bad_net")
    with pytest.raises(SkillSecurityError):
        scan_skill_source("eval('1+1')\n", skill_id="bad_eval")

    reg = SkillRegistry(tmp_path / "custom_skills", load_builtins=False)
    gate = SkillMutationGate(reg)
    synth = SkillSynthesizer(gate)

    in_schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    out_schema = {"type": "object", "required": ["y"], "properties": {"y": {"type": "integer"}}}

    good_fn = (
        "def run(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    return {'y': int(payload['x']) * 2}"
    )
    zero_fn = "def run(payload: dict[str, Any]) -> dict[str, Any]:\n    return {'y': 0}"
    bad_fn = "def run(payload: dict[str, Any]) -> dict[str, Any]:\n    return {'y': 999}"

    v1 = await synth.synthesize_and_register(
        skill_id="double_num",
        name="Doubler",
        description="Double integer x",
        tags=("math",),
        run_function_source=good_fn,
        input_schema=in_schema,
        output_schema=out_schema,
        test_vectors=[SkillTestVector({"x": 4}, lambda out: out["y"] == 8)],
    )
    assert v1.version == "1.0.0"

    # Reject backward-incompatible required input field addition!
    bad_in_schema = {
        "type": "object",
        "required": ["x", "z"],
        "properties": {"x": {"type": "integer"}, "z": {"type": "integer"}},
    }
    with pytest.raises(SkillMutationRejected, match="Backward-incompatible"):
        await synth.synthesize_and_register(
            skill_id="double_num",
            name="Doubler",
            description="Double",
            tags=("math",),
            run_function_source=zero_fn,
            input_schema=bad_in_schema,
            output_schema=out_schema,
            test_vectors=[SkillTestVector({"x": 4, "z": 1})],
        )

    # Reject mutation failing test vector predicate; registry stays on v1.0.0!
    with pytest.raises(SkillMutationRejected, match="predicate"):
        await synth.synthesize_and_register(
            skill_id="double_num",
            name="Doubler",
            description="Broken",
            tags=("math",),
            run_function_source=bad_fn,
            input_schema=in_schema,
            output_schema=out_schema,
            test_vectors=[SkillTestVector({"x": 4}, lambda out: out["y"] == 8)],
        )
    assert reg.get("double_num").version == "1.0.0"


def test_diary_empirical_proof_gate_post_mortem_and_context_budget(tmp_path: Path) -> None:
    """Verify INV-03 DiaryGateViolation, PostMortem evolution, and 20/30/50 ContextBudget."""
    talents_dir = tmp_path / "talents"
    tstore = TalentsStore(talents_dir)
    dev_charter = instantiate_charter("core_developer", instance_index=1)
    tstore.hire_or_create(dev_charter)
    diary = DiaryManager(talents_dir)

    # Attempting VERIFIED without EmpiricalProof MUST raise DiaryGateViolation (INV-03)!
    with pytest.raises(DiaryGateViolation):
        DiaryEntry(
            role_id="core_developer",
            project_id="p1",
            status="VERIFIED",
            observation="Async timeout race",
            lesson="Avoid unawaited tasks",
        )

    hyp = diary.append(
        DiaryEntry(
            role_id="core_developer",
            project_id="p1",
            status="HYPOTHESIS",
            observation="Suspected lock contention",
            lesson="Unverified guess",
        )
    )
    # Unverified hypothesis must NEVER appear in prompt rendering!
    assert diary.render_verified_for_prompt("core_developer") == "NONE"

    proof = EmpiricalProof(
        command="pytest tests/unit/test_lock.py",
        exit_code=0,
        artifact_sha256="deadbeef1234",
        observed_summary="1 passed in 0.04s",
    )
    diary.promote_hypothesis(
        hyp.model_copy(update={"lesson": "Never hold lock across await"}),
        proof,
    )
    prompt_diary = diary.render_verified_for_prompt("core_developer")
    assert "Never hold lock across await" in prompt_diary
    assert "Unverified guess" not in prompt_diary

    # Post-mortem distills VERIFIED diary into TalentsStore & AntipatternEntry
    pm = PostMortemAnalyzer(talents_store=tstore, diary_manager=diary)
    mmap = MentalMap(
        project_id="p1",
        project_name="P1",
        nodes={"N1": MapNode(id="N1", title="Lock", status="DONE", retry_count=1)},
    )
    report = pm.run_post_mortem(mmap, participating_roles={"core_developer"})
    assert "core_developer" in report.roles_evolved
    assert len(report.new_antipatterns) == 1
    evolved_prof = tstore.load("core_developer")
    assert evolved_prof is not None
    assert "Never hold lock across await" in evolved_prof.proven_heuristics

    # ContextBudgetAllocator enforces 20/30/50 split and truncates oversized inputs
    alloc = ContextBudgetAllocator(
        max_context_tokens=4_096,
        max_output_tokens=1_024,
        reasoning_budget=1_024,
    )
    plan = alloc.compute_plan()
    assert plan.usable_input_tokens < 2_048
    huge = "word " * 5_000
    fitted = alloc.fit_sections(persona=huge, submap=huge, task_and_skills=huge)
    assert fitted.truncated is True
    assert fitted.total_tokens <= plan.usable_input_tokens + 10
