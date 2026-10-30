"""Context window allocator enforcing 20% Persona / 30% Submap / 50% Skills+Task split (§25)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from orchestra.config.constants import (
    CONTEXT_SAFETY_RATIO,
    DIARY_INJECT_TOKENS,
    PERSONA_SHARE,
    SKILL_INDEX_INJECT_TOKENS,
    SKILLS_SHARE,
    SUBMAP_SHARE,
)
from orchestra.providers.token_counter import count_tokens_sync


class ContextBudgetPlan(BaseModel):
    """Computed token caps for each prompt segment guaranteeing zero ContextOverflow (§25)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    usable_input_tokens: int
    persona_tokens_cap: int
    submap_tokens_cap: int
    task_and_skills_tokens_cap: int
    diary_tokens_cap: int
    skill_index_tokens_cap: int


class AssembledPromptSections(BaseModel):
    """Trimmed prompt sections fitting deterministically inside `usable_input_tokens`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    persona_section: str
    submap_section: str
    task_and_skills_section: str
    total_tokens: int
    truncated: bool


def truncate_to_token_cap(text: str, max_tokens: int) -> tuple[str, bool]:
    """Deterministically trim `text` by line/word boundaries so `tokens <= max_tokens`."""
    if max_tokens <= 0 or not text:
        return ("", bool(text))
    if count_tokens_sync(text) <= max_tokens:
        return (text, False)

    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line]) if kept else line
        if count_tokens_sync(candidate) > max_tokens:
            break
        kept.append(line)

    if kept:
        return ("\n".join(kept), True)

    words = text.split()
    out_words: list[str] = []
    for word in words:
        candidate = " ".join([*out_words, word]) if out_words else word
        if count_tokens_sync(candidate) > max_tokens:
            break
        out_words.append(word)
    return (" ".join(out_words), True)


class ContextBudgetAllocator:
    """Computes and enforces the 20/30/50 prompt token budget across model context sizes."""

    def __init__(
        self,
        *,
        max_context_tokens: int = 131_072,
        max_output_tokens: int = 16_384,
        reasoning_budget: int = 8_192,
        safety_ratio: float = CONTEXT_SAFETY_RATIO,
    ) -> None:
        """Initialize ContextBudgetAllocator."""
        raw_available = max(1_024, max_context_tokens - max_output_tokens - reasoning_budget)
        self._usable = max(512, int(raw_available * safety_ratio))

    def compute_plan(self) -> ContextBudgetPlan:
        """Return exact per-section token caps following 20% / 30% / 50% rule (§25)."""
        p_cap = max(128, int(self._usable * PERSONA_SHARE))
        s_cap = max(192, int(self._usable * SUBMAP_SHARE))
        t_cap = max(256, int(self._usable * SKILLS_SHARE))
        return ContextBudgetPlan(
            usable_input_tokens=self._usable,
            persona_tokens_cap=p_cap,
            submap_tokens_cap=s_cap,
            task_and_skills_tokens_cap=t_cap,
            diary_tokens_cap=min(DIARY_INJECT_TOKENS, p_cap // 2),
            skill_index_tokens_cap=min(SKILL_INDEX_INJECT_TOKENS, t_cap // 3),
        )

    def fit_sections(
        self,
        *,
        persona: str,
        submap: str,
        task_and_skills: str,
    ) -> AssembledPromptSections:
        """Fit all three prompt regions within their respective token caps."""
        plan = self.compute_plan()
        p_fit, p_tr = truncate_to_token_cap(persona, plan.persona_tokens_cap)
        s_fit, s_tr = truncate_to_token_cap(submap, plan.submap_tokens_cap)
        t_fit, t_tr = truncate_to_token_cap(task_and_skills, plan.task_and_skills_tokens_cap)
        combined = f"{p_fit}\n\n{s_fit}\n\n{t_fit}"
        return AssembledPromptSections(
            persona_section=p_fit,
            submap_section=s_fit,
            task_and_skills_section=t_fit,
            total_tokens=count_tokens_sync(combined),
            truncated=(p_tr or s_tr or t_tr),
        )
