"""Canonical §10 dynamic skill synthesis module with ≥80% duplicate guard (§13.9.4)."""

from __future__ import annotations

from dataclasses import dataclass

from orchestra.skills.registry import SkillRegistry
from orchestra.skills.synthesizer import SkillSynthesizer


@dataclass(frozen=True, slots=True)
class SkillProposalVerdict:
    """Architect deduplication check result before synthesizing a new skill (§13.9.4)."""

    approved: bool
    duplicate_of: str | None = None
    similarity: float = 0.0


def check_skill_proposal_uniqueness(
    registry: SkillRegistry,
    *,
    proposed_description: str,
    threshold: float = 0.80,
) -> SkillProposalVerdict:
    """Reject skill proposals whose token Jaccard overlap with an active skill is >= threshold."""
    prop_tokens = {
        tok.strip(".,:;()[]{}").lower()
        for tok in proposed_description.split()
        if len(tok.strip(".,:;()[]{}")) >= 3
    }
    if not prop_tokens:
        return SkillProposalVerdict(approved=True)

    best_id: str | None = None
    best_sim = 0.0
    for spec in registry.manifest.skills.values():
        if spec.deprecated:
            continue
        existing_tokens = {
            tok.strip(".,:;()[]{}").lower()
            for tok in spec.description.split()
            if len(tok.strip(".,:;()[]{}")) >= 3
        }
        if not existing_tokens:
            continue
        union = prop_tokens | existing_tokens
        sim = len(prop_tokens & existing_tokens) / len(union)
        if sim > best_sim:
            best_sim = sim
            best_id = spec.id

    if best_sim >= threshold and best_id is not None:
        return SkillProposalVerdict(approved=False, duplicate_of=best_id, similarity=best_sim)
    return SkillProposalVerdict(approved=True, duplicate_of=best_id, similarity=best_sim)


__all__ = ["SkillProposalVerdict", "SkillSynthesizer", "check_skill_proposal_uniqueness"]
