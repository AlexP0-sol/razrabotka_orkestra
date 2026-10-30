"""Declarative blueprints for crypto roles (§38.11), escalations (§38.16), and 34-node DAG."""

from __future__ import annotations

from orchestra.domains.base import DomainRoleBlueprint, EscalationQuestionTemplate
from orchestra.state.mental_map import NodeType

CRYPTO_SKILL_MODULES: tuple[str, ...] = (
    "exchange_symbol_info",
    "decimal_money",
    "book_sync",
    "order_state_machine",
    "ws_replay",
    "clock_skew_check",
    "hmac_sign_request",
    "audit_log_append",
    "risk_limits_check",
    "backtest_report",
    "funding_rate_fetch",
    "data_quality_check",
)

_COMMON_FORBIDDEN = ("float_money", "naive_datetime", "hardcoded_exchange_secrets")
_COMMON_STYLE = (
    "NEVER float FOR MONEY OR PNL; ALWAYS Decimal WITH EXPLICIT ASSET (TR-01, TR-20)",
    "NEVER naive datetime; ALWAYS UTC TIMEZONE-AWARE (TR-02)",
    "EDUCATIONAL / ENGINEERING ARTIFACTS ONLY — NOT INVESTMENT ADVICE (DP-72)",
)

CRYPTO_ROLE_BLUEPRINTS: tuple[DomainRoleBlueprint, ...] = (
    DomainRoleBlueprint(
        role_id="exchange_gateway_engineer",
        title="Exchange Gateway Engineer",
        domain_tags=("crypto", "gateway", "rest", "websocket", "ccxt"),
        preferred_stack=("ccxt", "httpx", "websockets", "decimal"),
        forbidden_libs=_COMMON_FORBIDDEN,
        style_rules=(
            *_COMMON_STYLE,
            "ISOLATE RATE LIMITER, CLOCK SKEW, AND KILL SWITCH PER EXCHANGE (DP-82)",
            "SIGN REQUESTS USING SERVER TIME; NEVER LOG SIGNATURES OR SECRETS (TR-18, TR-19)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="market_data_engineer",
        title="Market Data Engineer",
        domain_tags=("crypto", "orderbook", "market_data", "parquet"),
        preferred_stack=("pyarrow", "websockets", "decimal"),
        forbidden_libs=(*_COMMON_FORBIDDEN, "ccxt_outside_gateway"),
        style_rules=(
            *_COMMON_STYLE,
            "RESYNC ORDERBOOK VIA SNAPSHOT + CONTINUOUS DELTAS AFTER ANY WS GAP (TR-10, DP-25)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="strategy_quant_engineer",
        title="Strategy Quant Engineer",
        domain_tags=("crypto", "strategy", "quant", "signals"),
        preferred_stack=("decimal", "pydantic"),
        forbidden_libs=(*_COMMON_FORBIDDEN, "ccxt", "httpx", "time.time"),
        style_rules=(
            *_COMMON_STYLE,
            "UNIFIED STRATEGY CODE ACROSS BACKTEST/PAPER/LIVE; EMIT OrderIntent ONLY (DP-04..05)",
            "NEVER CALL time.time() OR asyncio.sleep() INSIDE STRATEGY (DP-19)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="execution_engineer",
        title="Execution Engineer",
        domain_tags=("crypto", "execution", "orders", "fsm"),
        preferred_stack=("decimal", "pydantic"),
        forbidden_libs=_COMMON_FORBIDDEN,
        style_rules=(
            *_COMMON_STYLE,
            "EVERY ORDER REQUIRES UNIQUE clientOrderId AND PRE-SEND AUDIT APPEND (TR-03..04)",
            "TIMEOUT TRANSITIONS ORDER TO UNKNOWN -> RECONCILED BEFORE ANY RETRY (DP-07..08)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="risk_engineer",
        title="Risk Engineer",
        domain_tags=("crypto", "risk", "kill_switch", "reconciliation"),
        preferred_stack=("decimal", "pydantic"),
        forbidden_libs=_COMMON_FORBIDDEN,
        style_rules=(
            *_COMMON_STYLE,
            "PRE-TRADE RISK CHECKS FAIL-CLOSED AND CANNOT BE DISABLED BY CONFIG (TR-05)",
            "DEFAULT MODE PAPER; LIVE REQUIRES EXPLICIT HUMAN 'LIVE' CONFIRMATION (TR-07, DP-71)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="backtest_engineer",
        title="Backtest Engineer",
        domain_tags=("crypto", "backtest", "fill_model", "walk_forward"),
        preferred_stack=("decimal", "pyarrow", "pandas"),
        forbidden_libs=(*_COMMON_FORBIDDEN, "ccxt"),
        style_rules=(
            *_COMMON_STYLE,
            "EVENT-DRIVEN DETERMINISTIC BACKTEST WITH as_of LOOKAHEAD GUARD (TR-14..15, DP-41..44)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="data_platform_engineer",
        title="Data Platform Engineer",
        domain_tags=("crypto", "storage", "parquet", "postgresql"),
        preferred_stack=("pyarrow", "sqlalchemy", "pydantic"),
        forbidden_libs=_COMMON_FORBIDDEN,
        style_rules=(
            *_COMMON_STYLE,
            "PARTITION PARQUET BY SYMBOL AND UTC DAY; ENFORCE DATA QUALITY GATE (DP-50..51)",
        ),
    ),
    DomainRoleBlueprint(
        role_id="adversarial_qa_trading",
        title="Adversarial QA (Trading)",
        domain_tags=("crypto", "qa", "chaos", "hypothesis"),
        preferred_stack=("pytest", "hypothesis", "respx"),
        forbidden_libs=_COMMON_FORBIDDEN,
        style_rules=(
            *_COMMON_STYLE,
            "ATTACK WS GAPS, ORDER TIMEOUT->UNKNOWN, DUPLICATE FILLS, 429/418, CLOCK SKEW (DP-62)",
        ),
    ),
)

MANDATORY_CRYPTO_ESCALATIONS: tuple[EscalationQuestionTemplate, ...] = (
    EscalationQuestionTemplate(
        code="DP69_01_EXCHANGES_AND_MARKETS",
        blocks_s4=True,
        question_ru="Какие криптобиржи и типы рынков включить в конфигурацию запуска?",
        options_ru=("Spot + Perp через реестр exchanges.yaml (ccxt baseline)", "Только Spot"),
        recommendation_ru="Вариант 1 с пустым exchanges.enabled по умолчанию (fail-closed).",
    ),
    EscalationQuestionTemplate(
        code="DP69_02_TRADING_MODE",
        blocks_s4=True,
        question_ru="Какой целевой контур исполнения активировать по умолчанию?",
        options_ru=("PAPER (рекомендуется)", "TESTNET", "SHADOW"),
        recommendation_ru="PAPER (DP-06, DP-67).",
    ),
    EscalationQuestionTemplate(
        code="DP69_03_CAPITAL_AND_LOSS_LIMITS",
        blocks_s4=True,
        question_ru="Каковы лимиты капитала, max_daily_loss_quote и max_drawdown_pct?",
        options_ru=("Консервативный профиль (1% дневной лимит, 5% MaxDD)", "Задать вручную"),
        recommendation_ru="Консервативный профиль в конфиге риска.",
    ),
    EscalationQuestionTemplate(
        code="DP69_04_LEVERAGE",
        blocks_s4=False,
        question_ru="Разрешено ли кредитное плечо и каков предел max_leverage?",
        options_ru=("1x без плеча", "До 3x с контролем ликвидации"),
        recommendation_ru="1x для первой поставки.",
    ),
    EscalationQuestionTemplate(
        code="DP69_05_SYMBOL_ALLOWLIST",
        blocks_s4=True,
        question_ru="Какие торговые пары входят в белый и чёрный списки?",
        options_ru=("BTC/USDT и ETH/USDT", "Задать список в конфиге"),
        recommendation_ru="Явный allowlist ликвидных пар.",
    ),
    EscalationQuestionTemplate(
        code="DP69_06_KILL_SWITCH_POLICY",
        blocks_s4=True,
        question_ru="Какова политика действий при срабатывании Kill Switch?",
        options_ru=(
            "Отменить открытые ордера и заморозить новые (без рыночного закрытия позиций)",
            "Отменить ордера и закрыть позиции по рынку",
        ),
        recommendation_ru="Вариант 1 во избежание проскальзывания на неликвидном стакане.",
    ),
    EscalationQuestionTemplate(
        code="DP69_07_KEY_VAULT_AND_IP_WHITELIST",
        blocks_s4=False,
        question_ru="Какой способ хранения ключей и IP-whitelist применять для боевого контура?",
        options_ru=("OS keyring + обязательный IP-whitelist", "Зашифрованный vault-файл"),
        recommendation_ru="OS keyring + IP-whitelist (DP-29..30).",
    ),
    EscalationQuestionTemplate(
        code="DP69_08_HISTORICAL_DATA_DEPTH",
        blocks_s4=False,
        question_ru="Какой источник и глубина исторических данных нужны для бэктестов?",
        options_ru=("Локальные Parquet-дампы >= 12 месяцев", "REST выгрузка свечей и funding"),
        recommendation_ru="Офлайн Parquet-хранилище (DP-50, DP-53).",
    ),
    EscalationQuestionTemplate(
        code="DP69_09_TRADING_FREQUENCY",
        blocks_s4=False,
        question_ru="Каков целевой горизонт/частота сделок стратегий?",
        options_ru=("Среднечастотные (1m-1h бары / funding)", "Интрадей тики/стакан"),
        recommendation_ru="Среднечастотный контур.",
    ),
    EscalationQuestionTemplate(
        code="DP69_10_LATENCY_AND_REGION",
        blocks_s4=False,
        question_ru="Каковы требования к латентности и региону размещения?",
        options_ru=("Стандартный async Python (< 100 мс)", "Выделенный регион рядом с биржей"),
        recommendation_ru="Стандартный async контур с метриками RTT.",
    ),
    EscalationQuestionTemplate(
        code="DP69_11_DELISTING_POLICY",
        blocks_s4=False,
        question_ru="Как действовать при анонсе делистинга или принудительном закрытии позиции?",
        options_ru=("Запрет новых входов + алерт SITREP оператору", "Автозакрытие за 24ч"),
        recommendation_ru="Запрет новых ордеров + немедленный SITREP.",
    ),
    EscalationQuestionTemplate(
        code="DP69_12_REPORTING_AND_EXPORT",
        blocks_s4=False,
        question_ru="Требуются ли специальные выгрузки для бухгалтерского/налогового учёта?",
        options_ru=("CSV/JSON выгрузка всех исполнений и комиссий", "Не требуется"),
        recommendation_ru="Экспорт полного журнала сделок и комиссий.",
    ),
)

REFERENCE_CRYPTO_NODES: tuple[tuple[str, str, NodeType, str, tuple[str, ...]], ...] = (
    ("N01", "project-skeleton-config", "design", "exchange_gateway_engineer", ()),
    ("N02", "domain-types", "contract", "strategy_quant_engineer", ("N01",)),
    ("N03", "gateway-interface", "contract", "exchange_gateway_engineer", ("N02",)),
    ("N04", "strategy-interface", "contract", "strategy_quant_engineer", ("N02",)),
    ("N05", "risk-limits-contract", "contract", "risk_engineer", ("N02",)),
    ("N06", "persistence-schema", "contract", "data_platform_engineer", ("N02",)),
    ("N07", "request-signer", "code", "exchange_gateway_engineer", ("N03",)),
    ("N08", "rate-limiter", "code", "exchange_gateway_engineer", ("N03",)),
    ("N09", "ws-client", "code", "exchange_gateway_engineer", ("N03",)),
    ("N10", "book-sync", "code", "market_data_engineer", ("N03", "N09")),
    (
        "N11",
        "exchange-adapter",
        "code",
        "exchange_gateway_engineer",
        ("N03", "N07", "N08", "N09", "N10"),
    ),
    ("N12", "market-data-normalizer", "code", "market_data_engineer", ("N02", "N11")),
    ("N13", "data-quality-checks", "code", "data_platform_engineer", ("N12",)),
    ("N14", "market-data-store", "code", "data_platform_engineer", ("N06", "N12")),
    ("N15", "market-data-replay", "code", "market_data_engineer", ("N12", "N14")),
    ("N16", "order-state-machine", "code", "execution_engineer", ("N02",)),
    ("N17", "order-manager", "code", "execution_engineer", ("N03", "N11", "N16")),
    ("N18", "execution-gateways", "code", "execution_engineer", ("N03", "N17")),
    ("N19", "audit-log", "code", "data_platform_engineer", ("N06",)),
    ("N20", "pre-trade-risk-check", "code", "risk_engineer", ("N05", "N17")),
    ("N21", "kill-switch", "code", "risk_engineer", ("N20",)),
    ("N22", "reconciliation", "code", "risk_engineer", ("N11", "N17")),
    ("N23", "strategy-registry", "code", "strategy_quant_engineer", ("N04",)),
    ("N24", "backtest-engine", "code", "backtest_engineer", ("N15", "N18", "N23")),
    ("N25", "fill-model", "code", "backtest_engineer", ("N24",)),
    ("N26", "backtest-metrics", "code", "backtest_engineer", ("N24",)),
    ("N27", "backtest-report", "code", "backtest_engineer", ("N26",)),
    ("N28", "walk-forward-splitter", "code", "backtest_engineer", ("N24",)),
    ("N29", "trading-metrics-telemetry", "code", "data_platform_engineer", ("N19", "N22")),
    ("N30", "cli-runner", "code", "execution_engineer", ("N18", "N21", "N24")),
    ("N31", "example-strategy", "code", "strategy_quant_engineer", ("N04", "N23")),
    (
        "N32",
        "integration-e2e-paper",
        "test",
        "adversarial_qa_trading",
        ("N18", "N20", "N24", "N30", "N31"),
    ),
    ("N33", "chaos-suite", "test", "adversarial_qa_trading", ("N11", "N17", "N21", "N22")),
    ("N34", "docs-runbook-risk-disclaimer", "docs", "risk_engineer", ("N32", "N33")),
)
