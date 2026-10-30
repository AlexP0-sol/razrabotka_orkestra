"""Unit tests for Phase P11: all 10 mandatory meta-prompt templates and 20/30/50 PromptBuilder."""

from __future__ import annotations

import pytest

from orchestra.errors import ConfigurationError
from orchestra.prompts.prompt_builder import (
    REQUIRED_TEMPLATE_NAMES,
    PromptBuilder,
    PromptLayerItem,
    clean_sitrep_politeness_ru,
)


def test_all_ten_mandatory_templates_exist_and_load() -> None:
    """Verify DoD-28: all 10 templates from §28-§31 are present and non-empty."""
    builder = PromptBuilder(max_context_tokens=16_000)
    available = set(builder.available_templates())
    for required in REQUIRED_TEMPLATE_NAMES:
        assert required in available, f"Missing mandatory template: {required}.md"
        raw = builder.load_raw_template(required)
        assert len(raw.strip()) >= 200


def test_prompt_builder_dotted_rendering_strict_validation_and_truncation_audit() -> None:
    """Verify dotted placeholder substitution, strict missing-var error, and 20/30/50 truncation."""
    builder = PromptBuilder(
        max_context_tokens=2_048,
        max_output_tokens=512,
        reasoning_budget=512,
    )

    with pytest.raises(ConfigurationError):
        builder.render_template("qa_critic", {"node_id": "N1"}, strict=True)

    dev_vars = {
        "charter": {
            "title": "Senior Backend Engineer",
            "agent_id": "core_dev_01",
            "talent_role": "backend_python",
            "mission": "Implement order router",
            "scope_of_work": "src/router.py",
            "out_of_scope": "src/ui/*",
            "rules": "Zero floats for money",
        },
        "antipattern_registry": "AP-001: NO_FLOAT_MONEY",
        "diary_digest": "Use Decimal(str(val))",
        "node_id": "N_ROUTER",
        "node": {
            "kind": "code",
            "status": "IN_PROGRESS",
            "constraints": "<= 500 lines",
            "interface": {
                "inputs": "OrderRequest",
                "output": "OrderAck",
                "errors": "InsufficientMarginError",
            },
        },
        "neighbour_contracts_signatures": "class ExchangeGateway(Protocol): ...",
        "deps_status_lines": "N_GATEWAY: DONE",
        "skills_index": "import_verifier, ast_outline",
        "max_chunk_lines": 120,
    }

    oversized_local = PromptLayerItem(
        key="huge_unneeded_log",
        content="line of verbose trace output\n" * 600,
        priority=50,
    )
    built = builder.build_layered_prompt(
        template_name="developer",
        variables=dev_vars,
        neighbour_items=[
            PromptLayerItem(
                key="contract:C_GATEWAY",
                content="def submit(order: OrderRequest) -> OrderAck: ...",
                priority=1,
            )
        ],
        local_items=[oversized_local],
    )
    assert "Senior Backend Engineer" in built.rendered_core
    assert "huge_unneeded_log" in built.truncated_keys


def test_sitrep_politeness_cleaner_strips_russian_fluff() -> None:
    """Verify LP-12..14: polite greetings/apologies/sign-offs are stripped from Russian SITREP."""
    builder = PromptBuilder()
    rendered = builder.render_template(
        "sitrep",
        {
            "project_slug": "crypto_bot",
            "node_id": "N3",
            "status_fact": "Ожидание решения",
            "question_ru": "Какую биржу включить первой?",
            "options_block_ru": "1. Binance (высокая ликвидность)\n2. Bybit (единый аккаунт)",
            "recommendation_ru": "Вариант 1",
            "timeout_s": "3600с",
            "default_action_ru": "пауза узла N3",
        },
    )
    assert "[ПРОЕКТ: crypto_bot]" in rendered
    dirty_sitrep = (
        "Здравствуйте, уважаемый оператор!\n"
        "[ПРОЕКТ: crypto_bot]\nУЗЕЛ: N3\nСТАТУС: Ожидание решения\n"
        "С уважением, ваш Архитектор."
    )
    cleaned = clean_sitrep_politeness_ru(dirty_sitrep)
    assert "Здравствуйте" not in cleaned
    assert "С уважением" not in cleaned
    assert cleaned.startswith("[ПРОЕКТ: crypto_bot]")
