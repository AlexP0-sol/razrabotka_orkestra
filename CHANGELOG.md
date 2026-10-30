# Changelog

Все значимые изменения проекта **ORCHESTRA** документируются в этом файле.
Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] - 2026-10-30

### Added
- **Architectural Foundation (`P0`)**: 8-слойная архитектура на собственном `asyncio`-рантайме без внешних агентных фреймворков (`ADR-0001..ADR-0009`), `Makefile`, `Dockerfile`, `docker-compose.yml`, строгие профили `ruff`, `mypy --strict`, `pytest`, `.gitattributes` (`eol=lf`).
- **Config & LLM Provider Layer (`P1`)**: типизированные `Settings` (`pydantic-settings`), таблица констант `constants.py` (`§34.2`), иерархия исключений `OrchestraError`, `OpenAICompatProvider` (NVIDIA Build / DeepSeek V3.2 / Qwen / Llama), `CapabilityProber`, `MockProvider` и офлайн `TokenCounter`.
- **Multi-Key Pool & Dual-Stream Runtime (`P2`)**: `KeyPoolManager` с 5 состояниями ключей, ротацией на `429/5xx` без раскрытия секретов, `DualStreamRouter` с физической изоляцией `reasoning` от `content` (`INV-07`), `StructuredOutputValidator` с JSON-repair и `DualStreamLLMRuntime`.
- **MentalMap SSOT & Single-Writer StateStore (`P3`)**: ациклический граф `MentalMap` с 12 статусами FSM, эксклюзивным владением файлами и `SubMapProjection`, актор `StateStore`, хеш-цепочка `EventLog` (`prev_hash` SHA-256), атомарная запись с ретраями под Windows (`WIN-08`), реестры `ADR` и `AntipatternRegistry` и заморозка `ContractSignature`.
- **Dynamic Swarm, Charters & Talents (`P4`)**: неизменяемые `AgentCharter` с разделением полномочий автора и ревьюера, `OrgDesigner`, топологический планировщик с расчётом критического пути, гейты `G0..G4`, `DeadlockDetector`, `SwarmOrchestrator`, `TalentsStore`.
- **Telegraphic RPC & Non-Blocking Escalation (`P5`)**: валидатор телеграфного ASCII (`telegraphic.py`), `INTER_AGENT_RPC_V1` с лимитом 1 раунда, персистентный `EventBus` с JSONL outbox, неблокирующий `EscalationManager` (L1 → L2 → L3) и русскоязычный генератор `SITREP`.
- **Skills, Empirical Diary Gate & Context Budget (`P6`)**: `SkillRegistry`, 20 системных встроенных скиллов (включая все 10 из `§13.9.3`), `SkillExecutor`, `security_scan.py`, `SkillMutationGate`, `SkillSynthesizer` с защитой от дубликатов, `DiaryManager` с `EmpiricalProof` Gate, `PostMortemAnalyzer` и упаковщик контекста `20/30/50`.
- **Sandbox, PatchEngine, GitOps & IndependentJudge (`P7`)**: кроссплатформенная песочница (`Win32 Job Objects` / `POSIX setrlimit`, убийство дерева процессов по таймауту), транзакционный `PatchEngine` (`SEARCH_REPLACE`, `CREATE_FILE`, `UNIFIED_DIFF`, `CaseCollisionError`), `GitOps` с проверкой утечки секретов и детерминированный `IndependentJudge` с мутационным контролем.
- **Anti-Hallucination Research & Grounding (`P8`)**: `PreStudyRunner`, `RuntimeApiGrounder` (`HallucinatedApiError`), `JustInTimeDocReader` и офлайн `OfflineBM25Index`.
- **Operator Interfaces (`P9`)**: FastAPI + WebSocket консоль оператора с полностью локальной офлайн-статикой `orchestra/web/` (0 CDN), `TelegramBridge` с `fail-closed` whitelist и `telegram_keyboards.py` (`<= 64` байт `callback_data`), а также `Rich + Typer` CLI (`doctor`, `demo`, `init`, `run`, `status`, `keys`, `skills`, `talents`, `serve`).
- **Security, Telemetry & Resilience (`P10`)**: `SecretRedactor`, `PromptInjectionGuard`, `structlog` JSON-логирование, `MetricsCollector`, `CircuitBreaker`, `CheckpointManager` и `GracefulShutdownCoordinator`.
- **System Meta-Prompts (`P11`)**: 10 обязательных шаблонов `orchestra/prompts/templates/*.md` и `PromptBuilder` с 5-фазным протоколом мышления и бюджетированием `20/30/50`.
- **Crypto Trading Domain Pack (`P11b`)**: `CryptoTradingDomainPack` (8 доменных профессий, 12 доменных скиллов с сидируемыми `test_skill.py`, 13 AST-правил верификатора включая `NO_FLOAT_MONEY` и `LIVE_MODE_GUARD`, 34-узловой эталонный DAG с режимом `PAPER` по умолчанию, мультибиржевой реестр `exchanges.yaml`, `Amount`/`OrderState`/`PerExchangeRuntimeContext` и `docs/DOMAIN_CRYPTO_TRADING.md` с дисклеймером рисков).
- **Test Pyramid, Secret Scanner & Cross-Platform CI (`P12–P13`)**: полный набор unit, integration, property (`Hypothesis`), chaos и E2E тестов (`Todo API` + 34-узловая мультибиржевая крипто-база на фикстурах `binance` и `bybit`), `scripts/check_secrets_leak.py`, `scripts/bootstrap_demo.py`, `.github/workflows/ci.yml` (`ubuntu-latest` + `windows-latest`, Python 3.11/3.12 + Docker) и эксплуатационная документация (`PROTOCOLS.md`, `RUNBOOK.md`).

### Security
- Устранена утечка реального API-ключа в исходных аналитических материалах (`Pre-P0`, `8cf7612`), внедрены автоматический пре-коммит/CI сканер `scripts/check_secrets_leak.py` и потоковый `SecretRedactor`.
