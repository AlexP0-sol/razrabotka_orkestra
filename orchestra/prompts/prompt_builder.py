"""Layered meta-prompt loader, variable renderer, and 20/30/50 budget assembler (§25, §28-31)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestra.agents.context_budget import (
    AssembledPromptSections,
    ContextBudgetAllocator,
    truncate_to_token_cap,
)
from orchestra.errors import ConfigurationError
from orchestra.providers.token_counter import count_tokens_sync

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

REQUIRED_TEMPLATE_NAMES: tuple[str, ...] = (
    "architect",
    "developer",
    "qa_critic",
    "interview",
    "sitrep",
    "post_mortem",
    "skill_synthesis",
    "diary_entry",
    "patch_instructions",
    "self_review",
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

_RUSSIAN_POLITENESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*(?:здравствуйте|добрый\s+день|приветствую)[^.\n]*[.,!]?\s*"),
    re.compile(r"(?im)^\s*(?:пожалуйста|будьте\s+добры)[,\s]*"),
    re.compile(r"(?im)\s*(?:спасибо\s+за\s+ожидание|извините\s+за\s+беспокойство)[.,!]?\s*"),
    re.compile(r"(?im)\s*с\s+уважением[,\s].*$"),
)


def clean_sitrep_politeness_ru(text: str) -> str:
    """Strip conversational greetings, apologies, and sign-offs from Russian SITREPs (LP-12..14)."""
    cleaned = text
    for pattern in _RUSSIAN_POLITENESS_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def _lookup_dotted(variables: dict[str, Any], dotted_key: str) -> Any:
    if dotted_key in variables:
        return variables[dotted_key]
    current: Any = variables
    for part in dotted_key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            raise KeyError(dotted_key)
    return current


class PromptLayerItem(BaseModel):
    """Prioritized context fragment packed into one of the 20/30/50 prompt layers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    content: str
    priority: int = Field(default=10, ge=0)


class BuiltSystemPrompt(BaseModel):
    """Rendered and token-budgeted system prompt with truncation audit metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_name: str
    rendered_core: str
    sections: AssembledPromptSections
    truncated_keys: tuple[str, ...] = ()
    full_prompt_text: str


def _pack_layer_items(items: list[PromptLayerItem], token_cap: int) -> tuple[str, list[str]]:
    sorted_items = sorted(items, key=lambda it: (it.priority, it.key))
    kept_blocks: list[str] = []
    truncated_keys: list[str] = []
    used = 0
    for item in sorted_items:
        remaining = token_cap - used
        if remaining <= 0:
            truncated_keys.append(item.key)
            continue
        fitted, was_trunc = truncate_to_token_cap(item.content, remaining)
        if was_trunc:
            truncated_keys.append(item.key)
        if fitted:
            kept_blocks.append(fitted)
            used += count_tokens_sync(fitted)
    return ("\n\n".join(kept_blocks), truncated_keys)


class PromptBuilder:
    """Loads `orchestra/prompts/templates/*.md` and assembles 20/30/50 budgeted prompts (§25)."""

    def __init__(
        self,
        *,
        templates_dir: Path = TEMPLATES_DIR,
        max_context_tokens: int = 32_768,
        max_output_tokens: int = 4_096,
        reasoning_budget: int = 4_096,
    ) -> None:
        """Initialize PromptBuilder with 20/30/50 budget allocator."""
        self._templates_dir = templates_dir
        self._allocator = ContextBudgetAllocator(
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            reasoning_budget=reasoning_budget,
        )

    def available_templates(self) -> list[str]:
        """Return sorted list of `.md` template stems present on disk."""
        return sorted(p.stem for p in self._templates_dir.glob("*.md"))

    def load_raw_template(self, template_name: str) -> str:
        """Load raw markdown template by stem (e.g., `'architect'`, `'developer'`)."""
        clean_name = template_name.removesuffix(".md")
        path = self._templates_dir / f"{clean_name}.md"
        if not path.is_file():
            raise ConfigurationError(f"Prompt template not found: {path}")
        return path.read_text(encoding="utf-8")

    def render_template(
        self,
        template_name: str,
        variables: dict[str, Any],
        *,
        strict: bool = True,
    ) -> str:
        """Substitute `{{var}}` and `{{nested.var}}` placeholders in `template_name`."""
        raw = self.load_raw_template(template_name)
        missing: list[str] = []

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            try:
                val = _lookup_dotted(variables, key)
            except KeyError:
                missing.append(key)
                return f"{{{{{key}}}}}"
            if isinstance(val, list | tuple):
                return ", ".join(str(x) for x in val)
            return str(val)

        rendered = _PLACEHOLDER_RE.sub(_replace, raw)
        if strict and missing:
            uniq = ", ".join(sorted(set(missing)))
            raise ConfigurationError(
                f"Missing required template variables for '{template_name}': {uniq}"
            )
        if template_name.removesuffix(".md") == "sitrep":
            rendered = clean_sitrep_politeness_ru(rendered)
        return rendered

    def build_layered_prompt(
        self,
        *,
        template_name: str,
        variables: dict[str, Any],
        neighbour_items: list[PromptLayerItem] | None = None,
        local_items: list[PromptLayerItem] | None = None,
        strict: bool = True,
    ) -> BuiltSystemPrompt:
        """Render template into core 20% layer and combine with 30% neighbour & 50% local items."""
        core_text = self.render_template(template_name, variables, strict=strict)
        plan = self._allocator.compute_plan()
        p_text, p_tr = _pack_layer_items(
            [PromptLayerItem(key=f"template:{template_name}", content=core_text, priority=0)],
            plan.persona_tokens_cap,
        )
        s_text, s_tr = _pack_layer_items(neighbour_items or [], plan.submap_tokens_cap)
        t_text, t_tr = _pack_layer_items(local_items or [], plan.task_and_skills_tokens_cap)

        sections = self._allocator.fit_sections(
            persona=p_text,
            submap=s_text,
            task_and_skills=t_text,
        )
        full_parts = [
            part
            for part in (
                sections.persona_section,
                sections.submap_section,
                sections.task_and_skills_section,
            )
            if part
        ]
        return BuiltSystemPrompt(
            template_name=template_name,
            rendered_core=core_text,
            sections=sections,
            truncated_keys=(*p_tr, *s_tr, *t_tr),
            full_prompt_text="\n\n".join(full_parts),
        )
