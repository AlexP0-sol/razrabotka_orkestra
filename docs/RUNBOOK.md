# Руководство по эксплуатации ORCHESTRA (`RUNBOOK.md`, `DoD-33`)

Практическое руководство по установке, настройке пула ключей, запуску интерфейсов (CLI / Web / Telegram), проверке платформы и устранению нештатных ситуаций.

---

## 1. Системные требования

- **ОС**: Linux (Ubuntu 22.04+), macOS 14+, Windows 11 (нативная поддержка `Win32 Job Objects` и атомарной записи с ретраями `WIN-01..WIN-22`).
- **Python**: `3.11` или `3.12` (строго типизированный `asyncio`-рантайм).
- **Инструменты**: `git >= 2.35`; `docker` — опционально (при отсутствии демона песочница безопасно деградирует в `subprocess`-режим с `ResourceLimiter`, `WIN-20`).

---

## 2. Быстрая установка и диагностика (5 команд)

```bash
# 1. Создание виртуального окружения и установка закреплённых зависимостей
make install

# 2. Копирование шаблона окружения (для офлайн-демо ключи НЕ требуются)
cp .env.example .env

# 3. Полная самодиагностика платформы, песочницы, Git и реестра скиллов
.venv/bin/python -m orchestra doctor

# 4. Сквозной офлайн-прогон демо-сборки мультибиржевой базы (PAPER) на MockProvider
.venv/bin/python -m orchestra demo

# 5. Запуск веб-консоли оператора (FastAPI + офлайн DAG UI на http://127.0.0.1:8080)
.venv/bin/python -m orchestra serve --host 127.0.0.1 --port 8080
```

Альтернативный запуск демо через скрипт:
```bash
.venv/bin/python scripts/bootstrap_demo.py
```

Запуск в Docker:
```bash
docker compose up --build -d
```

---

## 3. Настройка API-ключей и переменных окружения (`.env`)

Все настройки загружаются через `orchestra.config.settings.Settings` (префикс `ORCHESTRA_`):

| Переменная | Назначение | Безопасный дефолт |
|---|---|---|
| `ORCHESTRA_PROFILE` | Профиль запуска (`dev` / `ci` / `prod`) | `dev` |
| `ORCHESTRA_RUNTIME_DIR` | Рабочий каталог состояния, проектов и кэша | `./runtime` |
| `ORCHESTRA_LLM__PROVIDER` | Провайдер инференса (`openai_compat` / `mock`) | `openai_compat` |
| `ORCHESTRA_LLM__BASE_URL` | Базовый URL OpenAI-совместимого API | `https://integrate.api.nvidia.com/v1` |
| `ORCHESTRA_LLM__DEFAULT_MODEL` | Основная модель с поддержкой reasoning | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| `ORCHESTRA_KEY_POOL__KEYS` | JSON-массив или список ключей через запятую | `[]` |
| `ORCHESTRA_SANDBOX__MODE` | Режим песочницы (`subprocess` / `docker`) | `subprocess` |
| `ORCHESTRA_WEB__ACCESS_TOKEN` | Bearer-токен для защиты REST/WS консоли | пусто в `dev`, обязателен при `0.0.0.0` |
| `ORCHESTRA_TELEGRAM__ENABLED` | Включение Telegram-бриджа оператора | `false` |
| `ORCHESTRA_TELEGRAM__ALLOWED_USER_IDS` | Белый список числовых ID операторов (`fail-closed`) | `[]` |

**Безопасность секретов (`SEC-01..05`, `DP-28..31`):**
- Никогда не коммитьте `.env`; перед каждым релизом и в CI автоматически выполняется `python scripts/check_secrets_leak.py .`.
- В крипто-домене биржевые ключи передаются только по имени переменной (`secret_ref`), создаются **строго без права вывода средств (`Withdraw = OFF`)**, а режим по умолчанию всегда `PAPER`.

---

## 4. Основные команды CLI (`python -m orchestra --help`)

- `orchestra doctor` — таблица готовности платформы (`WIN-22`), версии Git, режима песочницы, `CapabilityProber`, пула ключей и `/skills/` с итоговым вердиктом на русском.
- `orchestra init` — инициализация `runtime/` и манифеста встроенных скиллов.
- `orchestra demo` — эталонная сборка 34-узлового DAG мультибиржевой крипто-платформы на `MockProvider`.
- `orchestra run "<ТЗ или путь к файлу>" --project-id <id> [--mock]` — полный цикл сборки целевого проекта.
- `orchestra status --project-id <id>` — просмотр статусов узлов `MentalMap` и расхода токенов.
- `orchestra keys` — публичная телеметрия пула ключей (`ACTIVE`, `COOLING`, `DISABLED`, счётчики `429/5xx`) без раскрытия секретов.
- `orchestra skills [--crypto]` — каталог зарегистрированных системных и доменных скиллов.
- `orchestra talents` — версии и `first_pass_rate` накопленных профилей ролей.
- `orchestra serve --host 127.0.0.1 --port 8080` — запуск офлайн веб-дашборда и WebSocket-хаба `/ws`.

---

## 5. Диагностика и устранение типовых сбоев (Troubleshooting)

### 5.1. Шторм `HTTP 429 Too Many Requests` на одном из ключей
- **Симптом**: в `orchestra keys` ключ перешёл в статус `COOLING`.
- **Поведение системы**: `KeyPoolManager` автоматически уважает `Retry-After` (или `KEY_COOLDOWN_429_S = 60` с) и мгновенно маршрутизирует запросы на здоровые ключи пула без обрыва задачи агента (`T-03`).
- **Действие оператора**: при постоянной нехватке квоты добавьте дополнительный ключ на лету через `POST /api/v1/keys` или в `.env`.

### 5.2. Внезапное падение процесса или отключение питания (`SIGKILL`)
- **Симптом**: после рестарта часть узлов осталась в `IN_PROGRESS` или `LEASED` с просроченной арендой, либо повреждён `mental_map.json`.
- **Поведение системы**: при старте `StateStore` проверяет SHA-256 чексумму `mental_map.json`, при сбое откатывается на ротируемый `.bak` снапшот и доигрывает хвост `events.jsonl` с проверкой `prev_hash`, а `CheckpointManager.recover_after_crash()` идемпотентно возвращает зависшие узлы в `READY` (`T-02`).

### 5.3. Узел в статусе `BLOCKED_HUMAN`
- **Симптом**: бизнес-развилка требует решения человека; соседние ветви DAG продолжают выполняться.
- **Действие оператора**: откройте панель эскалаций в Web UI (`GET /api/v1/questions` → `POST /api/v1/questions/{id}/answer`) или нажмите inline-кнопку под сообщением SITREP в Telegram.

### 5.4. Скилл переведён в `QUARANTINE`
- **Симптом**: в манифесте скилла `quarantined = true`.
- **Причина**: провал регрессионного теста в `SkillMutationGate` или обнаружение запрещённого вызова в `security_scan.py`.
- **Действие оператора**: проверьте `test_skill.py` в `runtime/skills/<skill_id>/`, исправьте дефект и повторно прогоните `SkillMutationGate`.

### 5.5. Ошибка верификатора `LIVE_MODE_GUARD`
- **Причина**: сгенерированный код обращается к `TradingMode.LIVE` без явного гейта человеческого подтверждения (`DP-71`, `DoD-43`).
- **Действие оператора**: сохраняйте `PAPER`-режим по умолчанию; перевод в `LIVE` допустим только после `SHADOW/TESTNET` прогона и прохождения всех 8 обязательных вопросов эскалации `§38.16`.
