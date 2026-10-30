"""Agent Charter models and structural separation-of-duties guards (§18.1, INV-04, BAN-07)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import MAX_CHUNK_LINES, REASONING_DEV
from orchestra.errors import OrchestraError

RoleTier = Literal["permanent", "dynamic", "crypto_domain"]


class IndependentReviewViolationError(OrchestraError):
    """Raised when an author agent attempts self-approval or lacks judge authority (INV-04)."""


class AgentCharter(BaseModel):
    """Immutable specification of an agent's authority, mission, tools, and KPIs (§18.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    role_id: str
    display_name: str
    tier: RoleTier = "dynamic"
    mission: str
    responsibilities: tuple[str, ...]
    forbidden_actions: tuple[str, ...] = (
        "Never mark own node DONE without Independent Judge verification",
        "Never leak reasoning stream into code, patches, contracts, or RPC",
        "Never modify frozen contracts without Lead Architect unfreeze",
    )
    allowed_tools: tuple[str, ...] = ("read_file", "submit_patch", "invoke_skill", "send_rpc")
    owned_node_types: tuple[str, ...] = ("code",)
    kpis: tuple[str, ...] = ("first_pass_verify_rate",)
    reasoning_budget: int = Field(default=REASONING_DEV, ge=0)
    max_chunk_lines: int = Field(default=MAX_CHUNK_LINES, ge=50, le=1_000)
    can_approve_patches: bool = False
    can_unfreeze_contracts: bool = False
    can_mutate_skills: bool = False


def assert_independent_reviewer(
    *,
    author_agent_id: str,
    reviewer_charter: AgentCharter,
) -> None:
    """Enforce structural separation of duties between author and verifier (INV-04, BAN-07)."""
    if not reviewer_charter.can_approve_patches:
        raise IndependentReviewViolationError(
            f"Agent '{reviewer_charter.agent_id}' (role='{reviewer_charter.role_id}') does not "
            "hold patch approval authority."
        )
    if reviewer_charter.agent_id == author_agent_id:
        raise IndependentReviewViolationError(
            f"Self-review is structurally forbidden (INV-04): author '{author_agent_id}' "
            "cannot approve its own artifact."
        )
