"""Catalog of permanent core roles, dynamic engineering roles, and §38.3 crypto roles."""

from __future__ import annotations

from typing import Any

from orchestra.agents.charter import AgentCharter
from orchestra.config.constants import (
    MAX_AGENTS_HARD_CAP,
    REASONING_ARCHITECT,
    REASONING_DEV,
    REASONING_QA,
    REASONING_UI,
)
from orchestra.errors import ConfigurationError

PERMANENT_ROLE_TEMPLATES: dict[str, AgentCharter] = {
    "lead_architect": AgentCharter(
        agent_id="lead_architect_01",
        role_id="lead_architect",
        display_name="Lead Architect & Swarm Director",
        tier="permanent",
        mission="Design acyclic Mental Map, freeze formal contracts, and resolve escalations.",
        responsibilities=(
            "Decompose requirements into DAG nodes with explicit acceptance criteria",
            "Author ADRs and freeze interface contracts before S6 build",
            "Communicate Russian SITREPs and structured escalations to human customer",
        ),
        allowed_tools=(
            "read_file",
            "mutate_map",
            "freeze_contract",
            "unfreeze_contract",
            "send_rpc",
        ),
        owned_node_types=("design", "contract", "gate"),
        kpis=("zero_contract_drift", "dag_parallelism_efficiency"),
        reasoning_budget=REASONING_ARCHITECT,
        can_unfreeze_contracts=True,
    ),
    "qa_adversary": AgentCharter(
        agent_id="qa_adversary_01",
        role_id="qa_adversary",
        display_name="Adversarial QA & Edge-Case Engineer",
        tier="permanent",
        mission="Synthesize >=10 edge/chaos/property tests per node to break weak implementations.",
        responsibilities=(
            "Write unit, integration, property-based, and chaos test suites before/alongside code",
            "Probe boundary conditions, race conditions, and malformed inputs",
        ),
        allowed_tools=("read_file", "submit_patch", "invoke_skill", "send_rpc"),
        owned_node_types=("test",),
        kpis=("defect_detection_rate", "mutation_kill_score"),
        reasoning_budget=REASONING_QA,
    ),
    "independent_judge": AgentCharter(
        agent_id="independent_judge_01",
        role_id="independent_judge",
        display_name="Independent Verification Judge",
        tier="permanent",
        mission="Run deterministic sandbox verifiers and render objective PASS/REWORK verdicts.",
        responsibilities=(
            "Run ruff, mypy --strict, pytest, secret scan, and domain AST verifiers in sandbox",
            "Reject any artifact with placeholders, skipped tests, or contract hash drift",
        ),
        allowed_tools=("read_file", "run_verifier", "transition_node", "send_rpc"),
        owned_node_types=("integration", "gate"),
        kpis=("zero_false_positives_in_main",),
        reasoning_budget=REASONING_QA,
        can_approve_patches=True,
    ),
    "skill_curator": AgentCharter(
        agent_id="skill_curator_01",
        role_id="skill_curator",
        display_name="Skill & Memory Curator",
        tier="permanent",
        mission="Synthesize reusable verified skills, enforce Mutation Gate, and curate diaries.",
        responsibilities=(
            "Validate new and mutated skills through static security scan and unit tests",
            "Distill verified post-mortem lessons into /agent_talents/ and antipatterns",
        ),
        allowed_tools=("read_file", "register_skill", "mutate_skill", "invoke_skill", "send_rpc"),
        owned_node_types=("research", "docs"),
        kpis=("skill_reuse_rate", "zero_broken_skill_mutations"),
        reasoning_budget=REASONING_DEV,
        can_mutate_skills=True,
    ),
}

DYNAMIC_ROLE_TEMPLATES: dict[str, AgentCharter] = {
    "core_developer": AgentCharter(
        agent_id="core_developer_01",
        role_id="core_developer",
        display_name="Core Systems Python Developer",
        tier="dynamic",
        mission="Implement clean, typed, <=300-line Python chunks adhering strictly to contracts.",
        responsibilities=(
            "Implement assigned code nodes",
            "Fix verifier defects in <=2 iterations",
        ),
        owned_node_types=("code",),
        reasoning_budget=REASONING_DEV,
    ),
    "api_backend_developer": AgentCharter(
        agent_id="api_backend_developer_01",
        role_id="api_backend_developer",
        display_name="Async API & Services Developer",
        tier="dynamic",
        mission="Implement FastAPI, WebSocket, and async integration endpoints.",
        responsibilities=("Build HTTP/WS handlers", "Maintain strict Pydantic v2 schemas"),
        owned_node_types=("code", "integration"),
        reasoning_budget=REASONING_DEV,
    ),
    "frontend_ui_developer": AgentCharter(
        agent_id="frontend_ui_developer_01",
        role_id="frontend_ui_developer",
        display_name="Offline Vanilla Web UI Developer",
        tier="dynamic",
        mission="Build zero-CDN offline vanilla ES2022 + CSS Grid/Flexbox operator dashboards.",
        responsibilities=("Implement static web UI", "Ensure throttled DOM updates"),
        owned_node_types=("code",),
        reasoning_budget=REASONING_UI,
    ),
    "research_specialist": AgentCharter(
        agent_id="research_specialist_01",
        role_id="research_specialist",
        display_name="Pre-Study & API Grounding Researcher",
        tier="dynamic",
        mission="Inspect installed package ASTs and docs so zero hallucinated APIs are used.",
        responsibilities=("Verify symbols via inspect/importlib", "Populate docs_cache"),
        owned_node_types=("research",),
        reasoning_budget=REASONING_DEV,
    ),
    "docs_writer": AgentCharter(
        agent_id="docs_writer_01",
        role_id="docs_writer",
        display_name="Technical Documentation & Runbook Author",
        tier="dynamic",
        mission="Produce accurate architecture docs, ADRs, protocols, and operational runbooks.",
        responsibilities=("Write README, RUNBOOK, PROTOCOLS, and CHANGELOG",),
        owned_node_types=("docs",),
        reasoning_budget=REASONING_UI,
    ),
}

CRYPTO_ROLE_TEMPLATES: dict[str, AgentCharter] = {
    "quant_strategist": AgentCharter(
        agent_id="quant_strategist_01",
        role_id="quant_strategist",
        display_name="Quant Strategist",
        tier="crypto_domain",
        mission="Design alpha signals strictly free of look-ahead bias using Decimal arithmetic.",
        responsibilities=("Implement signal generators", "Enforce causal bar boundaries"),
        owned_node_types=("code", "design"),
        reasoning_budget=REASONING_ARCHITECT,
    ),
    "market_data_engineer": AgentCharter(
        agent_id="market_data_engineer_01",
        role_id="market_data_engineer",
        display_name="Market Data Engineer",
        tier="crypto_domain",
        mission="Build gap-free OHLCV/orderbook pipelines with monotonic sequence checks.",
        responsibilities=("Normalize multi-exchange feeds", "Handle WS reconnect snapshots"),
        owned_node_types=("code",),
        reasoning_budget=REASONING_DEV,
    ),
    "execution_oms_engineer": AgentCharter(
        agent_id="execution_oms_engineer_01",
        role_id="execution_oms_engineer",
        display_name="Execution & OMS Engineer",
        tier="crypto_domain",
        mission="Implement idempotent order state machines with deterministic clientOrderId.",
        responsibilities=("Enforce LOT_SIZE/PRICE_FILTER rounding", "Handle partial fills"),
        owned_node_types=("code", "integration"),
        reasoning_budget=REASONING_ARCHITECT,
    ),
    "risk_guardian": AgentCharter(
        agent_id="risk_guardian_01",
        role_id="risk_guardian",
        display_name="Pre-Trade Risk Guardian",
        tier="crypto_domain",
        mission="Enforce non-bypassable PreTradeRiskGate, drawdown caps, and KillSwitch.",
        responsibilities=("Validate max notional, leverage, daily loss, and liquidation distance",),
        owned_node_types=("code", "gate"),
        reasoning_budget=REASONING_ARCHITECT,
    ),
    "backtest_validator": AgentCharter(
        agent_id="backtest_validator_01",
        role_id="backtest_validator",
        display_name="Backtest & Overfitting Validator",
        tier="crypto_domain",
        mission="Run walk-forward validation across 0/2/5/10 bps slippage + taker/funding fees.",
        responsibilities=("Detect overfitting and survivorship bias", "Produce backtest metrics"),
        owned_node_types=("test", "integration"),
        reasoning_budget=REASONING_QA,
    ),
    "exchange_connector_specialist": AgentCharter(
        agent_id="exchange_connector_specialist_01",
        role_id="exchange_connector_specialist",
        display_name="Exchange Connector Specialist",
        tier="crypto_domain",
        mission="Implement signed REST/WS adapters obeying exchanges.yaml rate weights.",
        responsibilities=("Implement HMAC/Ed25519 signing", "Respect HTTP 429/418 bans"),
        owned_node_types=("code",),
        reasoning_budget=REASONING_DEV,
    ),
    "ops_reliability_engineer": AgentCharter(
        agent_id="ops_reliability_engineer_01",
        role_id="ops_reliability_engineer",
        display_name="Crypto Ops & Reliability Engineer",
        tier="crypto_domain",
        mission="Maintain clock skew sync, state reconciliation, and PAPER default safety.",
        responsibilities=("Implement startup/60s reconciliation", "Enforce LIVE safety gates"),
        owned_node_types=("code", "docs"),
        reasoning_budget=REASONING_DEV,
    ),
}

ALL_ROLE_TEMPLATES: dict[str, AgentCharter] = {
    **PERMANENT_ROLE_TEMPLATES,
    **DYNAMIC_ROLE_TEMPLATES,
    **CRYPTO_ROLE_TEMPLATES,
}


def instantiate_charter(
    role_id: str,
    *,
    instance_index: int = 1,
    overrides: dict[str, Any] | None = None,
) -> AgentCharter:
    """Instantiate an `AgentCharter` from the catalog with a unique `agent_id`."""
    template = ALL_ROLE_TEMPLATES.get(role_id)
    if template is None:
        raise ConfigurationError(f"Неизвестный шаблон роли агента: '{role_id}'")
    updates: dict[str, Any] = {"agent_id": f"{role_id}_{instance_index:02d}"}
    if overrides:
        updates.update(overrides)
    return template.model_copy(update=updates)


def validate_team_roster(
    charters: list[AgentCharter] | tuple[AgentCharter, ...],
    *,
    hard_cap: int = MAX_AGENTS_HARD_CAP,
) -> None:
    """Validate roster size, unique agent_ids, and mandatory permanent roles (§18)."""
    if len(charters) > hard_cap:
        raise ConfigurationError(
            f"Превышен жёсткий лимит численности роя: {len(charters)} > {hard_cap}"
        )
    ids = [c.agent_id for c in charters]
    if len(ids) != len(set(ids)):
        raise ConfigurationError("Дублирующиеся agent_id в составе команды роя")
    present_roles = {c.role_id for c in charters}
    missing = set(PERMANENT_ROLE_TEMPLATES) - present_roles
    if missing:
        raise ConfigurationError(
            f"В составе роя отсутствуют обязательные постоянные роли: {sorted(missing)}"
        )
