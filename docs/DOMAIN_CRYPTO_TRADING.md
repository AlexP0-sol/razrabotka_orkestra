# Доменный пакет: Криптобиржи и Алгоритмические Стратегии (`docs/DOMAIN_CRYPTO_TRADING.md`)

> **RISK DISCLAIMER (ОБЯЗАТЕЛЬНОЕ УВЕДОМЛЕНИЕ О РИСКАХ — DP-72..DP-75):**
> Данное программное обеспечение и все демонстрационные стратегии являются исключительно
> инженерной инфраструктурой и учебными примерами (`EDUCATIONAL EXAMPLE — NOT INVESTMENT ADVICE`).
> Система **не предоставляет инвестиционных рекомендаций**. Торговля цифровыми активами и
> деривативами сопряжена с высоким риском полной потери капитала. Результаты моделирования
> на исторических данных (бэктестов) **не гарантируют** будущую доходность. По умолчанию
> активен безопасный режим `PAPER`; включение `LIVE` заблокировано без явного письменного
> подтверждения человека и прохождения чек-листа безопасности (`DP-71`).

---

## 1. Назначение пакета (`orchestra/domains/crypto_trading.py`)

Доменный пакет вооружает фабрику **ORCHESTRA** специализированными механизмами для построения
мультибиржевых торговых платформ и исследовательских контуров:

1. **8 доменных профессий** (`§38.11`) в `/agent_talents/` со строгими `forbidden_libs`, `style_rules`
   и чистым `DISCOVERY_DIARY.md` (пополняемым только через `EmpiricalProof` Gate):
   - `exchange_gateway_engineer`, `market_data_engineer`, `strategy_quant_engineer`,
     `execution_engineer`, `risk_engineer`, `backtest_engineer`, `data_platform_engineer`,
     `adversarial_qa_trading`.
2. **12 исполняемых доменных скиллов** (`§38.12`) в `/skills/`, каждый с `skill.yaml`, `skill.py`
   и `test_skill.py`:
   - `exchange_symbol_info`, `decimal_money`, `book_sync`, `order_state_machine`, `ws_replay`,
     `clock_skew_check`, `hmac_sign_request` (строго по `secret_ref`), `audit_log_append`,
     `risk_limits_check`, `backtest_report`, `funding_rate_fetch`, `data_quality_check`.
3. **13 детерминированных AST-правил судьи** (`§38.13`, `orchestra/domains/crypto_ast_verifier.py`):
   - `NO_FLOAT_MONEY`, `NO_NAIVE_DATETIME`, `NO_WALLCLOCK_IN_STRATEGY`,
     `NO_DIRECT_EXCHANGE_IMPORT`, `NO_LOOKAHEAD`, `MANDATORY_CLIENT_ORDER_ID`,
     `ORDER_AUDIT_REQUIRED`, `NO_UNBOUNDED_AVERAGING`, `DECIMAL_JSON_SAFETY`,
     `SECRET_NOT_LITERAL`, `LIVE_MODE_GUARD`, `NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER`,
     `NO_FLOAT_CROSS_EXCHANGE`.
4. **Мультибиржевая изоляция с первого дня** (`§38.19`, `exchanges.yaml`, `ExchangeRegistry`):
   - Пустой список `enabled: []` по умолчанию (`fail-closed`).
   - Изолированные `ExchangeTokenBucket`, `ClockSkew`, `ExchangeKillSwitch` на каждую биржу.
   - Добавление новой биржи выполняется добавлением адаптера в `gateway/adapters/` и записи
     в `exchanges.yaml` без изменения кода ядра (`DoD-49`).
5. **34-узловая эталонная декомпозиция `MentalMap`** (`§38.15`):
   - Контракты `N02..N06` замораживаются первыми (`S5`), после чего 5 параллельных инженерных
     веток собираются роем и проверяются chaos/property-тестами (`N32..N33`).
