# Прогресс реализации и приёмки ORCHESTRA (`docs/PROGRESS.md`)

Точка возобновления и итоговый реестр приёмки (`FMT-09`, `FP-08`, `§35`, `DL-02`).

## 1. Статус фаз плана (`Pre-P0` → `P13`)

| Фаза | Область поставки | Статус | Коммит | Дата (UTC) |
|---|---|---|---|---|
| **Pre-P0** | Аудит отчёта предыдущего агента, санитизация секрета `nvapi-*`, синхронизация ТЗ `v1.2.1` | `DONE` | `8cf7612` | 2026-10-30 |
| **P0** | Архитектурный фундамент (`ARCHITECTURE.md`, ADR-0001..0009, конфиги, дерево пакетов, `Makefile`, Docker) | `DONE` | `96aaa7c` | 2026-10-30 |
| **P1** | Конфигурация (`Settings`, `constants`, `models_registry`), `errors.py` и слой провайдеров (`base`, `capabilities`, `openai_compat`, `mock`, `token_counter`) | `DONE` | `3b41bff` | 2026-10-30 |
| **P2** | Пул ключей (`KeyPoolManager`) и LLM-рантайм (`llm_client`, `stream_router`, `structured_output`) | `DONE` | `ac3a430` | 2026-10-30 |
| **P3** | Слой состояния (`mental_map`, `state_store`, `event_log`, `adr`, `antipatterns`, `contracts`) | `DONE` | `cf5bfa9` | 2026-10-30 |
| **P4** | Роли и рой (`charter`, `agent_runtime`, `roles_catalog`, `talents_store`, `swarm/*`) | `DONE` | `487dd8b` | 2026-10-30 |
| **P5** | Коммуникация (`rpc_protocol`, `event_bus`, `telegraphic`, `escalation`) | `DONE` | `7f5ae34` | 2026-10-30 |
| **P6** | Скиллы и память (`skills/*`, 20 системных builtin-скиллов, `diary_manager`, `post_mortem`, `context_budget`) | `DONE` | `e7cf869` | 2026-10-30 |
| **P7** | Верификация и платформенный слой Windows/POSIX (`sandbox`, `verifiers`, `patch_engine`, `git_ops`, `judge`) | `DONE` | `400114f` | 2026-10-30 |
| **P8** | Исследование и анти-галлюцинации (`pre_study`, `doc_reader`, `grounding`, `web_search`) | `DONE` | `72977a9` | 2026-10-30 |
| **P9** | Человеческие интерфейсы (`telegram_bridge`, `telegram_keyboards`, `api_server`, `ws_events`, `web/`, `cli`) | `DONE` | `a7ac3c8` | 2026-10-30 |
| **P10** | Наблюдаемость, безопасность, отказоустойчивость (`telemetry/*`, `observability/*`, `secrets`, `injection_guard`) | `DONE` | `f331c02` | 2026-10-30 |
| **P11** | Системные мета-промпты (`orchestra/prompts/templates/*.md` + `prompt_builder.py`) | `DONE` | `fc39e4b` | 2026-10-30 |
| **P11b** | Доменный пакет крипто-трейдинга (`domains/base.py`, `crypto_trading.py`, 12 крипто-скиллов, 13 AST-правил, `DOMAIN_CRYPTO_TRADING.md`) | `DONE` | `43966a5` | 2026-10-30 |
| **P12** | Полный набор тестов (`unit`, `integration`, `e2e`, `chaos`, `property`), `check_secrets_leak.py`, CI-матрица | `DONE` | `0138063` | 2026-10-30 |
| **P13** | Финальная документация (`RUNBOOK.md`, `PROTOCOLS.md`, `CHANGELOG.md`), `bootstrap_demo.py`, приёмка по `DoD-01…DoD-49` | `DONE` | `HEAD` | 2026-10-30 |

---

## 2. Матрица соответствия `DoD-01..DoD-49` (`§35`)

| Пункт | Статус | Доказательство (файл / тест / команда) |
|---|---|---|
| **DoD-01** | `[x]` | Полное дерево `§10` проверено `test_dod01_dod04_dod10_dod34_repository_completeness_and_zero_placeholders` |
| **DoD-02** | `[x]` | `Makefile` (`install`, `dev`, `lint`, `typecheck`, `test`), `orchestra/interfaces/{cli.py,api_server.py}` |
| **DoD-03** | `[x]` | `python -m orchestra doctor` (`orchestra/interfaces/cli.py`, русскоязычная таблица и вердикт) |
| **DoD-04** | `[x]` | `.env.example` без секретов, `runtime/` исключён в `.gitignore`, `scripts/check_secrets_leak.py` = 0 findings |
| **DoD-05** | `[x]` | `Dockerfile` (non-root user, healthcheck) + `docker-compose.yml` + job `docker-build` в `.github/workflows/ci.yml` |
| **DoD-06** | `[x]` | `ruff check orchestra tests scripts` и `ruff format --check` — 0 ошибок |
| **DoD-07** | `[x]` | `mypy --strict orchestra tests scripts` — 0 ошибок |
| **DoD-08** | `[x]` | `pytest --cov=orchestra --cov-fail-under=85 -q` — все тесты зелёные, общее покрытие `>= 86%` |
| **DoD-09** | `[x]` | `test_import_graph_acyclic_layered_and_file_line_caps` (`T-13`, `<= 500` строк/файл, 0 циклов) |
| **DoD-10** | `[x]` | `test_dod01_dod04_dod10_dod34_repository_completeness_and_zero_placeholders` (`0` `TODO`/`NotImplementedError`) |
| **DoD-11** | `[x]` | `orchestra/inference/stream_router.py`, `test_t04_and_dod11_dual_stream_separation_and_exact_token_accounting` |
| **DoD-12** | `[x]` | `orchestra/inference/key_pool.py`, `test_t03_key_pool_429_storm_rotates_seamlessly_to_healthy_key`, `test_t08_*` |
| **DoD-13** | `[x]` | `orchestra/state/{mental_map.py,state_store.py}`, 196 кейсов `test_fsm_exhaustive_transition_matrix`, `test_t02_*` |
| **DoD-14** | `[x]` | `orchestra/agents/charter.py` (`frozen=True`, `assert_independent_reviewer`), `test_p4_agents_talents_and_swarm.py` |
| **DoD-15** | `[x]` | `orchestra/swarm/{org_designer.py,scheduler.py,orchestrator.py,deadlock.py}`, `test_t09_non_blocking_escalation_*` |
| **DoD-16** | `[x]` | `orchestra/comms/{rpc_protocol.py,telegraphic.py,event_bus.py}`, `test_canonical_rpc_pair_fits_under_120_tokens` |
| **DoD-17** | `[x]` | `orchestra/skills/{registry.py,executor.py,synthesis.py,mutation_gate.py,security_scan.py}`, `test_t10_and_t11_*` |
| **DoD-18** | `[x]` | `orchestra/talents/{talents_store.py,diary_manager.py,post_mortem.py}`, `DiaryGateViolation`, `test_t10_and_t11_*` |
| **DoD-19** | `[x]` | `orchestra/verify/{sandbox.py,verifiers.py,judge.py}`, `test_p7_sandbox_patch_git_and_judge.py` |
| **DoD-20** | `[x]` | `orchestra/verify/patch_engine.py`, `test_prop_04_patch_engine_roundtrip_and_rollback_atomicity` (`T-14`) |
| **DoD-21** | `[x]` | `orchestra/verify/git_ops.py` (`GT-07`), `orchestra/skills/builtin/git_commit.py` |
| **DoD-22** | `[x]` | `orchestra/research/{pre_study.py,grounding.py,doc_reader.py,web_search.py}`, `test_p8_research_and_grounding.py` |
| **DoD-23** | `[x]` | `orchestra/interfaces/{telegram_bridge.py,telegram_keyboards.py}`, `test_p9_interfaces_web_tg_cli.py` |
| **DoD-24** | `[x]` | `orchestra/interfaces/{api_server.py,ws_events.py}` + офлайн `orchestra/web/{index.html,app.js,styles.css,vendor/}` |
| **DoD-25** | `[x]` | `orchestra/interfaces/cli.py` (`version`, `init`, `doctor`, `demo`, `keys`, `skills`, `talents`, `status`, `run`, `serve`) |
| **DoD-26** | `[x]` | `orchestra/observability/{logging_setup.py,metrics.py}` + `orchestra/telemetry/*` + `SecretRedactor` |
| **DoD-27** | `[x]` | `orchestra/observability/resilience.py` (`CircuitBreaker`, `CheckpointManager`, `GracefulShutdownCoordinator`) |
| **DoD-28** | `[x]` | `orchestra/prompts/templates/*.md` (10 шаблонов) + `orchestra/prompts/prompt_builder.py` (`20/30/50`) |
| **DoD-29** | `[x]` | `orchestra/comms/telegraphic.py` (машинный ASCII) + `format_russian_sitrep` (русский SITREP) |
| **DoD-30** | `[x]` | 5 фаз `REASONING PROTOCOL` во всех ролевых шаблонах (`test_p11_meta_prompts_and_builder.py`) |
| **DoD-31** | `[x]` | `ARCHITECTURE.md` (слои `L0..L7`, инварианты, потоки данных) |
| **DoD-32** | `[x]` | `docs/PROTOCOLS.md` (все регламенты `§14–§26`) |
| **DoD-33** | `[x]` | `docs/RUNBOOK.md` (установка, `.env`, эксплуатация, troubleshooting) |
| **DoD-34** | `[x]` | `docs/adr/ADR-0001..ADR-0009` (9 принятых ADR, включая `ADR-0001` и `ADR-0002`) |
| **DoD-35** | `[x]` | `docs/DEFAULTS.md` (реестр инженерных дефолтов и разрешённых коллизий `C-01..C-15`) |
| **DoD-36** | `[x]` | `README.md` (быстрый старт за 5 команд, ASCII-схема дашборда, ссылки на документацию) |
| **DoD-37** | `[x]` | `python -m orchestra demo` и `python scripts/bootstrap_demo.py` работают офлайн без ключей |
| **DoD-38** | `[x]` | `orchestra/domains/crypto_trading.py` (8 профессий, 12 скиллов, 13 AST-правил, 8 обязательных эскалаций) |
| **DoD-39** | `[x]` | Сидирование `runtime/skills/` + `test_skill.py` (72/72 зелёных тест-кейса доменных скиллов) |
| **DoD-40** | `[x]` | `orchestra/domains/crypto_ast_verifier.py` + `test_t17_each_crypto_rule_positive_and_negative` |
| **DoD-41** | `[x]` | `tests/e2e/test_factory_builds_trading_base.py` (`T-16`: 34 узла DAG, `PAPER` по умолчанию) |
| **DoD-42** | `[x]` | `orchestra/domains/crypto_types.py` (`Amount`, `OrderState.UNKNOWN`, `PerExchangeRuntimeContext`) + `IndependentJudge` |
| **DoD-43** | `[x]` | Правило `LIVE_MODE_GUARD` (`ERROR` без подтверждения человека) покрыто unit/E2E тестами |
| **DoD-44** | `[x]` | Дисклеймер рисков в `docs/DOMAIN_CRYPTO_TRADING.md` + `check_secrets_leak.py` в `test_t16_*` (`0` утечек) |
| **DoD-45** | `[x]` | Раздел «Платформа» и деградация `docker -> subprocess` (`WIN-20`, `WIN-22`) в `orchestra doctor` |
| **DoD-46** | `[x]` | `ExecutionSandbox` (`Job Objects` / `setrlimit`, убийство дерева процессов) + `test_t02_*` |
| **DoD-47** | `[x]` | `test_windows_portability_case_collision_atomic_retry_and_gitattributes` (`WIN-07..13`) |
| **DoD-48** | `[x]` | `.github/workflows/ci.yml` (`ubuntu-latest` + `windows-latest`, Python 3.11/3.12 + Docker) |
| **DoD-49** | `[x]` | `exchanges.yaml`, `ExchangeRegistry.register_adapter` (тест `FakeX` без правки ядра), фикстуры `binance` + `bybit` |

**Раздел «НЕ ВЫПОЛНЕНО» (`DL-03`):** пусто — все 49 пунктов `DoD-01..DoD-49` выполнены и подтверждены автотестами.
