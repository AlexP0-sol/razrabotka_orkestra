"""Skill synthesizer wrapping pure transformation bodies into CLI-ready gated skills (§20.3)."""

from __future__ import annotations

from typing import Any

from orchestra.skills.manifest import SkillSpec
from orchestra.skills.mutation_gate import SkillMutationGate, SkillTestVector


def render_skill_module_source(*, function_body_source: str) -> str:
    """Wrap a `def run(payload: dict[str, Any]) -> dict[str, Any]: ...` into a full CLI skill."""
    return (
        '"""Auto-synthesized ORCHESTRA executable skill."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import sys\n"
        "from typing import Any\n\n\n"
        f"{function_body_source.strip()}\n\n\n"
        "def _main() -> None:\n"
        "    raw = sys.stdin.read()\n"
        "    payload: dict[str, Any] = json.loads(raw) if raw.strip() else {}\n"
        "    result = run(payload)\n"
        "    sys.stdout.write(json.dumps(result))\n\n\n"
        'if __name__ == "__main__":\n'
        "    _main()\n"
    )


class SkillSynthesizer:
    """Synthesizes and registers new skills strictly through `SkillMutationGate` (SK-09..12)."""

    def __init__(self, gate: SkillMutationGate) -> None:
        """Initialize SkillSynthesizer."""
        self._gate = gate

    async def synthesize_and_register(
        self,
        *,
        skill_id: str,
        name: str,
        description: str,
        tags: tuple[str, ...],
        run_function_source: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        test_vectors: list[SkillTestVector],
    ) -> SkillSpec:
        """Render complete skill file, verify through Mutation Gate, and register in manifest."""
        full_source = render_skill_module_source(function_body_source=run_function_source)
        return await self._gate.propose_skill(
            skill_id=skill_id,
            name=name,
            description=description,
            tags=tags,
            source_code=full_source,
            input_schema=input_schema,
            output_schema=output_schema,
            test_vectors=test_vectors,
        )
