"""SkillRegistry managing built-in and synthesized skills in `runtime/skills/` (§20.1)."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestra.errors import SkillError
from orchestra.skills.manifest import SkillManifest, SkillSpec
from orchestra.skills.security_scan import scan_skill_source
from orchestra.state.event_log import atomic_write_text

if TYPE_CHECKING:
    from orchestra.skills.executor import SkillExecutor

BUILTIN_SKILL_MODULES: tuple[str, ...] = (
    "search_documentation",
    "read_project_ast",
    "run_sandbox_test",
    "apply_patch",
    "append_to_diary",
    "escalate_to_architect",
    "git_commit",
    "verify_api_exists",
    "count_tokens",
    "list_dir_tree",
    "ast_outline",
    "dependency_graph",
    "contract_compat_check",
    "json_schema_validator",
    "complexity_metrics",
    "test_coverage_gap",
    "secret_leak_scan",
    "dead_code_finder",
    "api_signature_diff",
    "import_verifier",
)


class SkillRegistry:
    """Persistent registry of verified executable skills backed by `manifest.json`."""

    def __init__(self, skills_dir: Path, *, load_builtins: bool = True) -> None:
        """Initialize SkillRegistry and optionally register all 10 built-in system skills."""
        self._dir = skills_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._dir / "manifest.json"
        self._manifest = self._load_manifest()
        if load_builtins:
            self._register_builtin_modules(
                package_prefix="orchestra.skills.builtin",
                module_names=BUILTIN_SKILL_MODULES,
            )

    @property
    def manifest(self) -> SkillManifest:
        """Return current immutable SkillManifest."""
        return self._manifest

    def get(self, skill_id: str) -> SkillSpec:
        """Lookup a registered skill by `skill_id` or raise `SkillError`."""
        spec = self._manifest.skills.get(skill_id)
        if spec is None:
            raise SkillError(f"Unknown skill '{skill_id}'")
        return spec

    def register_custom_skill(
        self,
        *,
        skill_id: str,
        name: str,
        version: str,
        description: str,
        tags: tuple[str, ...],
        source_code: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        timeout_s: float = 30.0,
    ) -> SkillSpec:
        """Scan, write to `runtime/skills/<skill_id>.py`, and register a custom skill."""
        scan_skill_source(source_code, skill_id=skill_id)
        target_file = self._dir / f"{skill_id}.py"
        if target_file.is_file():
            bak_dir = self._dir / "backups"
            bak_dir.mkdir(parents=True, exist_ok=True)
            prev_ver = (
                self._manifest.skills[skill_id].version
                if skill_id in self._manifest.skills
                else "0.0.0"
            )
            atomic_write_text(
                bak_dir / f"{skill_id}.v{prev_ver}.py",
                target_file.read_text(encoding="utf-8"),
            )
        atomic_write_text(target_file, source_code)
        digest = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        existing = self._manifest.skills.get(skill_id)
        spec = SkillSpec(
            id=skill_id,
            name=name,
            version=version,
            description=description,
            tags=tags,
            entrypoint_path=str(target_file.resolve()),
            input_schema=input_schema,
            output_schema=output_schema,
            timeout_s=timeout_s,
            builtin=False,
            calls_total=existing.calls_total if existing else 0,
            calls_succeeded=existing.calls_succeeded if existing else 0,
            sha256=digest,
        )
        self.upsert_spec(spec)
        return spec

    def upsert_spec(self, spec: SkillSpec) -> None:
        """Persist updated `SkillSpec` into `manifest.json`."""
        skills = dict(self._manifest.skills)
        skills[spec.id] = spec
        self._manifest = SkillManifest(version=self._manifest.version + 1, skills=skills)
        atomic_write_text(self._manifest_path, self._manifest.model_dump_json(indent=2))

    async def invoke(
        self,
        skill_id: str,
        payload: dict[str, Any],
        *,
        executor: SkillExecutor,
    ) -> dict[str, Any]:
        """Execute `skill_id` via `executor` and record empirical invocation statistics."""
        spec = self.get(skill_id)
        try:
            out = await executor.execute(spec, payload)
        except Exception:
            self.upsert_spec(spec.record_invocation(succeeded=False))
            raise
        self.upsert_spec(spec.record_invocation(succeeded=True))
        return out

    def search_by_tags(self, tags: set[str]) -> list[SkillSpec]:
        """Return non-deprecated skills matching one or more `tags`."""
        wanted = {t.lower() for t in tags}
        matched = [
            s
            for s in self._manifest.skills.values()
            if not s.deprecated and ({t.lower() for t in s.tags} & wanted)
        ]
        matched.sort(key=lambda s: (s.success_rate, s.id), reverse=True)
        return matched

    def register_package_modules(
        self,
        *,
        package_prefix: str,
        module_names: tuple[str, ...],
    ) -> None:
        """Register built-in skill modules from a Python subpackage (e.g. crypto builtins)."""
        self._register_builtin_modules(package_prefix=package_prefix, module_names=module_names)

    def _register_builtin_modules(
        self,
        *,
        package_prefix: str,
        module_names: tuple[str, ...],
    ) -> None:
        skills = dict(self._manifest.skills)
        changed = False
        for mod_name in module_names:
            mod = importlib.import_module(f"{package_prefix}.{mod_name}")
            meta: dict[str, Any] = mod.SKILL_METADATA
            mod_file = Path(str(mod.__file__)).resolve()
            src = mod_file.read_text(encoding="utf-8")
            scan_skill_source(src, skill_id=str(meta["id"]))
            digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
            sid = str(meta["id"])
            prev = skills.get(sid)
            if prev is not None and prev.sha256 == digest:
                continue
            skills[sid] = SkillSpec(
                id=sid,
                name=str(meta["name"]),
                version=str(meta.get("version", "1.0.0")),
                description=str(meta["description"]),
                tags=tuple(meta.get("tags", ())),
                entrypoint_path=str(mod_file),
                input_schema=dict(meta["input_schema"]),
                output_schema=dict(meta["output_schema"]),
                builtin=True,
                calls_total=prev.calls_total if prev else 0,
                calls_succeeded=prev.calls_succeeded if prev else 0,
                sha256=digest,
            )
            changed = True
        if changed:
            self._manifest = SkillManifest(version=self._manifest.version + 1, skills=skills)
            atomic_write_text(self._manifest_path, self._manifest.model_dump_json(indent=2))

    def _load_manifest(self) -> SkillManifest:
        if not self._manifest_path.is_file():
            return SkillManifest()
        try:
            return SkillManifest.model_validate_json(
                self._manifest_path.read_text(encoding="utf-8")
            )
        except Exception:
            return SkillManifest()
