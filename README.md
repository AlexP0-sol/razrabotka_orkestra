# ORCHESTRA

**Автономная фабрика разработки ПО с динамическим эволюционирующим роем LLM-агентов,
детерминированным судьёй качества и доменным пакетом алгоритмического крипто-трейдинга.**

ORCHESTRA превращает сырое человеческое ТЗ (через Telegram, Web-дашборд или CLI) в работающий,
протестированный и закоммиченный проект: проводит интервью по белым пятнам, проверяет актуальные
версии пакетов, собирает команду агентов под задачу, замораживает контракты (`Skeleton First`),
пишет тесты до кода (`TDD`), проверяет каждый атом `≤ 300` строк конвейером
`ast.parse → ruff → mypy --strict → pytest` и накапливает верифицированный опыт между проектами.

---

## Быстрый старт (5 команд)

```bash
git clone https://github.com/AlexP0-sol/razrabotka_orkestra.git && cd razrabotka_orkestra
make install
python setup_env.py
.venv/bin/python -m orchestra doctor
.venv/bin/python -m orchestra demo
```

Поднять resident-ядро с offline Web-дашбордом (`http://localhost:8000`):

```bash
make dev
```

---

## Превью Web-дашборда (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ ORCHESTRA v0.1.0  │ Project: trading-base │ Mode: PAPER │ Tokens: 48.2k / 200.0k     │
├───────────────────────────────────────────┬──────────────────────────────────────────┤
│ MENTAL MAP DAG (Cytoscape.js, offline)    │ LIVE REASONING STREAM (per-agent panels) │
│                                           │                                          │
│   [01-config DONE]                        │ [agent_01_gateway | budget 8192]         │
│          │                                │ 1 BOUNDARY: NO FLOAT, UTC ONLY           │
│   [02-domain-types DONE] (FROZEN)         │ 2 DIARY: CHECK WS SEQ CONTINUITY         │
│       ├──┴────────────┐                   │ 3 VULN: HANDLE UNKNOWN ON TIMEOUT        │
│ [16-order-fsm DONE] [10-book-sync VERIFY] │                                          │
│       │                   │               │ [agent_02_risk | budget 8192]            │
│ [17-order-mgr IMPL] [11-adapter READY]    │ 4 SKELETON: OrderIntent -> RiskDecision  │
├───────────────────────────────────────────┼──────────────────────────────────────────┤
│ KEY POOL: k_91a4 FREE │ k_3c0f LEASED(1)  │ ESCALATIONS (SITREP RU): 0 open          │
│ JUDGE: ast OK │ ruff OK │ mypy OK │ test  │ RPC BUS: 14 msgs (avg 58 tok, ASCII)     │
└───────────────────────────────────────────┴──────────────────────────────────────────┘
```

---

## Документация проекта и спецификации

| Документ | Назначение |
|---|---|
| [`docs/ORCHESTRA_USER_GUIDE_RU_DARK.pdf`](docs/ORCHESTRA_USER_GUIDE_RU_DARK.pdf) | Полное иллюстрированное руководство пользователя на русском языке в тёмной теме (PDF) |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Архитектура по слоям `L0–L7`, потоки данных, конкурентность, Windows/POSIX, `DomainPack` |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Установка, настройка ключей, запуск на Windows/Linux/Docker, диагностика сбоев |
| [`docs/PROTOCOLS.md`](docs/PROTOCOLS.md) | Все инженерные регламенты (`Telegraphic RPC`, `SITREP`, `Skeleton First`, `TDD`, `Gates`) |
| [`docs/DOMAIN_CRYPTO_TRADING.md`](docs/DOMAIN_CRYPTO_TRADING.md) | Доменный пакет криптобирж и алго-стратегий (`TR-01…TR-20`, мультибиржевость `§38.19`) |
| [`docs/DEFAULTS.md`](docs/DEFAULTS.md) | Решения по умолчанию (`D-01…D-19`) и таблица разрешённых противоречий (`C-01…C-15`) |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Статус реализации фаз `P0…P13` и контрольные коммиты |
| [`docs/adr/`](docs/adr/) | Реестр архитектурных решений (`ADR-0001` … `ADR-0009`) |
| [`prompts/MASTER_PROMPT.md`](prompts/MASTER_PROMPT.md) | Исчерпывающее техническое задание v1.2.1 (~600 требований, 15 фаз поставки, 49 пунктов DoD) |
| [`prompts/SOURCE_ANALYSIS.md`](prompts/SOURCE_ANALYSIS.md) | Анализ исходного диалога `razrabotka_orkestra.html` и карта покрытия требований |

---

## Безопасность

1. В ранней ревизии исходного файла `razrabotka_orkestra.html` присутствовал открытый ключ
   NVIDIA Build (`nvapi-…`). В рабочей ветке и текущем файле он вычищен (`nvapi-REDACTED`), но
   остаётся в раннем коммите git-истории `048c9d8` — **ключ необходимо отозвать/перевыпустить
   в кабинете NVIDIA Build**.
2. В самой ORCHESTRA активна автоматическая защита секретов: `SecretStr` в `Settings`, запрет
   передачи `.env` оркестратора в песочницу, принудительная маскировка `nvapi-*`/`sk-*`/токенов
   в логах и pre-commit/CI-сканер утечек (`scripts/check_secrets_leak.py`).
