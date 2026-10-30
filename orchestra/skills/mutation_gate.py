"""Skill Mutation Gate enforcing AST safety, schema compat, and test vectors (§21, INV-06)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestra.errors import SkillMutationRejected, SkillSecurityError
from orchestra.skills.executor import SkillExecutor
from orchestra.skills.manifest import SkillSpec
from orchestra.skills.registry import SkillRegistry
from orchestra.skills.security_scan import scan_skill_source


@dataclass(frozen=True)
class SkillTestVector:
    """Input payload and assertion predicate verified inside isolated subprocess."""

    input_payload: dict[str, Any]
    predicate: Callable[[dict[str, Any]], bool] | None = None


def bump_minor_semver(version: str) -> str:
    """Increment minor component of `MAJOR.MINOR.PATCH` string (`1.0.0 -> 1.1.0`)."""
    parts = version.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return "1.1.0"
    return f"{int(parts[0])}.{int(parts[1]) + 1}.0"


def check_schema_backward_compatible(
    *,
    old_input: dict[str, Any],
    new_input: dict[str, Any],
    old_output: dict[str, Any],
    new_output: dict[str, Any],
) -> None:
    """Verify mutation neither adds mandatory input fields nor drops output properties (§21)."""
    old_req = set(old_input.get("required", ()))
    new_req = set(new_input.get("required", ()))
    added_required = new_req - old_req
    if added_required:
        raise SkillMutationRejected(
            f"Backward-incompatible input_schema: added required fields {sorted(added_required)}"
        )

    old_out_props = set(old_output.get("properties", {}).keys())
    new_out_props = set(new_output.get("properties", {}).keys())
    removed_outputs = old_out_props - new_out_props
    if removed_outputs:
        raise SkillMutationRejected(
            f"Backward-incompatible output_schema: removed fields {sorted(removed_outputs)}"
        )


class SkillMutationGate:
    """Guards `/skills/` against broken, insecure, or backward-incompatible edits (INV-06)."""

    def __init__(self, registry: SkillRegistry, executor: SkillExecutor | None = None) -> None:
        """Initialize SkillMutationGate."""
        self._registry = registry
        self._executor = executor or SkillExecutor()

    async def propose_skill(
        self,
        *,
        skill_id: str,
        name: str,
        description: str,
        tags: tuple[str, ...],
        source_code: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        test_vectors: list[SkillTestVector],
        version: str | None = None,
    ) -> SkillSpec:
        """Validate a new or mutated skill in a temporary sandbox before committing to registry."""
        if not test_vectors:
            raise SkillMutationRejected("At least one SkillTestVector is required by Mutation Gate")

        try:
            scan_skill_source(source_code, skill_id=skill_id)
        except SkillSecurityError as exc:
            raise SkillMutationRejected(f"Security scan failed: {exc}") from exc

        existing = self._registry.manifest.skills.get(skill_id)
        if existing is not None:
            check_schema_backward_compatible(
                old_input=existing.input_schema,
                new_input=input_schema,
                old_output=existing.output_schema,
                new_output=output_schema,
            )
            target_version = version or bump_minor_semver(existing.version)
        else:
            target_version = version or "1.0.0"

        with tempfile.TemporaryDirectory(prefix="orch_skill_gate_") as tmp:
            candidate_file = Path(tmp) / f"{skill_id}.py"
            candidate_file.write_text(source_code, encoding="utf-8", newline="\n")
            candidate_spec = SkillSpec(
                id=skill_id,
                name=name,
                version=target_version,
                description=description,
                tags=tags,
                entrypoint_path=str(candidate_file),
                input_schema=input_schema,
                output_schema=output_schema,
                timeout_s=15.0,
            )
            for idx, vec in enumerate(test_vectors):
                try:
                    out = await self._executor.execute(candidate_spec, vec.input_payload)
                except Exception as exc:
                    raise SkillMutationRejected(
                        f"Test vector #{idx} execution failed for '{skill_id}': {exc}"
                    ) from exc
                if vec.predicate is not None and not vec.predicate(out):
                    raise SkillMutationRejected(
                        f"Test vector #{idx} predicate returned False on output: {out}"
                    )

        return self._registry.register_custom_skill(
            skill_id=skill_id,
            name=name,
            version=target_version,
            description=description,
            tags=tags,
            source_code=source_code,
            input_schema=input_schema,
            output_schema=output_schema,
        )
