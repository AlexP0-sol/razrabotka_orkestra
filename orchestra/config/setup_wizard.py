"""Interactive CLI wizard to populate `.env` sequentially with space-to-skip UX."""

from __future__ import annotations

import builtins
import contextlib
import json
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orchestra.config import constants as c

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class WizardCollectedConfig:
    """Validated user inputs collected by the interactive `.env` setup wizard."""

    provider: str
    base_url: str
    default_model: str
    api_keys: tuple[str, ...]
    runtime_dir: str
    telegram_enabled: bool
    telegram_bot_token: str | None
    telegram_allowed_user_ids: tuple[int, ...]
    web_host: str
    web_port: int
    web_access_token: str | None


def _is_skip(raw: str) -> bool:
    """Return True when the user entered empty input or whitespace/space(s) only."""
    return raw.strip() == ""


def mask_secret_preview(secret: str) -> str:
    """Return safe masked preview of a secret without exposing its value."""
    cleaned = secret.strip()
    if len(cleaned) <= 10:
        return "*" * len(cleaned)
    return f"{cleaned[:6]}...{cleaned[-4:]}"


def collect_wizard_inputs(
    *,
    input_fn: InputFn = builtins.input,
    output_fn: OutputFn = builtins.print,
    max_key_slots: int = c.MAX_AGENTS_HARD_CAP,
) -> WizardCollectedConfig:
    """Prompt the user step-by-step for model, API keys, and optional settings.

    Sending a single space (`" "`) or pressing Enter on an empty line skips optional
    fields and stops sequential API-key / user-ID collection immediately.
    """
    output_fn("=" * 72)
    output_fn("  Мастер первичной настройки ORCHESTRA (.env)")
    output_fn("  Подсказка: отправьте ПРОБЕЛ (или пустой Enter), чтобы пропустить поле")
    output_fn("  или завершить ввод списка ключей на нужном вам количестве.")
    output_fn("=" * 72)

    # 1. Base URL
    output_fn("\n[1/6] Адрес OpenAI-совместимого API провайдера")
    output_fn(f"      По умолчанию (пробел): {c.DEFAULT_NVIDIA_BASE_URL}")
    raw_url = input_fn("  Введите Base URL (или пробел для NVIDIA Build): ")
    base_url = c.DEFAULT_NVIDIA_BASE_URL if _is_skip(raw_url) else raw_url.strip()

    # 2. Single LLM Model
    output_fn("\n[2/6] Рабочая модель LLM (одна основная модель для роя)")
    output_fn(f"      По умолчанию (пробел): {c.DEFAULT_MODEL_ID}")
    output_fn("      Примеры: deepseek-ai/deepseek-v3.2 | qwen/qwen3-235b-a22b")
    raw_model = input_fn("  Введите ID одной модели (или пробел для модели по умолчанию): ")
    default_model = c.DEFAULT_MODEL_ID if _is_skip(raw_model) else raw_model.strip()

    # 3. Sequential API keys (up to max_key_slots; space stops / skips remaining)
    output_fn(f"\n[3/6] Поочерёдный ввод API-ключей LLM (слотов: до {max_key_slots})")
    output_fn("      Введите столько ключей, сколько хотите использовать сейчас.")
    output_fn(
        "      Когда введёте нужное количество — отправьте ПРОБЕЛ, чтобы пропустить остальные."
    )
    collected_keys: list[str] = []
    seen_keys: set[str] = set()
    for slot in range(1, max_key_slots + 1):
        suffix = (
            " (или пробел -> режим mock без ключей)"
            if slot == 1
            else " (или пробел -> завершить ввод ключей)"
        )
        raw_key = input_fn(f"  API-ключ #{slot}{suffix}: ")
        if _is_skip(raw_key):
            output_fn(f"  -> Принято ключей: {len(collected_keys)}. Остальные слоты пропущены.")
            break
        key_val = raw_key.strip()
        if key_val in seen_keys:
            output_fn("  [!] Такой ключ уже был добавлен выше — дубликат пропущен.")
            continue
        seen_keys.add(key_val)
        collected_keys.append(key_val)
        output_fn(f"  [+] Ключ #{len(collected_keys)} сохранён ({mask_secret_preview(key_val)})")

    provider = "openai_compat" if collected_keys else "mock"
    if provider == "mock":
        output_fn("  [i] Ключи не введены: установлен безопасный офлайн-режим provider=mock.")

    # 4. Local runtime folder
    output_fn("\n[4/6] Локальная папка на компьютере для всех сгенерированных проектов")
    output_fn("      По умолчанию (пробел): ./runtime")
    raw_rt = input_fn("  Путь к папке runtime (или пробел для ./runtime): ")
    runtime_dir = "./runtime" if _is_skip(raw_rt) else raw_rt.strip()

    # 5. Optional Telegram bridge
    output_fn("\n[5/6] Telegram-бот для уведомлений и ответов на вопросы (необязательно)")
    raw_tg_token = input_fn("  Токен Telegram-бота (отправьте пробел, если не используете): ")
    tg_token: str | None = None
    tg_users: list[int] = []
    tg_enabled = False
    if not _is_skip(raw_tg_token):
        tg_token = raw_tg_token.strip()
        output_fn("  Введите разрешённые числовые Telegram User ID (пробел — завершить):")
        while True:
            raw_uid = input_fn(f"  Telegram User ID #{len(tg_users) + 1} (или пробел): ")
            if _is_skip(raw_uid):
                break
            uid_txt = raw_uid.strip()
            if not uid_txt.isdigit():
                output_fn("  [!] Telegram User ID должен состоять только из цифр. Повторите.")
                continue
            uid_int = int(uid_txt)
            if uid_int not in tg_users:
                tg_users.append(uid_int)
        if tg_users:
            tg_enabled = True
        else:
            output_fn(
                "  [i] Не указан ни один User ID: Telegram оставлен выключенным (fail-closed)."
            )

    # 6. Optional Web Console settings
    output_fn("\n[6/6] Локальная Web-консоль оператора (http://127.0.0.1:8000)")
    raw_web_tok = input_fn(
        "  Пароль/токен для Web-дашборда (или пробел для локального запуска без пароля): "
    )
    web_token = None if _is_skip(raw_web_tok) else raw_web_tok.strip()
    web_host = "0.0.0.0" if web_token else "127.0.0.1"

    return WizardCollectedConfig(
        provider=provider,
        base_url=base_url,
        default_model=default_model,
        api_keys=tuple(collected_keys),
        runtime_dir=runtime_dir,
        telegram_enabled=tg_enabled,
        telegram_bot_token=tg_token,
        telegram_allowed_user_ids=tuple(tg_users),
        web_host=web_host,
        web_port=8000,
        web_access_token=web_token,
    )


def render_env_content(cfg: WizardCollectedConfig) -> str:
    """Serialize `WizardCollectedConfig` into a valid `.env` file with LF line endings."""
    keys_json = json.dumps(list(cfg.api_keys), ensure_ascii=False)
    tg_ids_json = json.dumps(list(cfg.telegram_allowed_user_ids))
    lines = [
        "# Generated by ORCHESTRA Setup Wizard (LOCAL ONLY — gitignored, never commit!)",
        "ORCHESTRA_PROFILE=dev",
        f"ORCHESTRA_RUNTIME_DIR={cfg.runtime_dir}",
        "",
        "# --- LLM Provider & Model ---",
        f"ORCHESTRA_LLM__PROVIDER={cfg.provider}",
        f"ORCHESTRA_LLM__BASE_URL={cfg.base_url}",
        f"ORCHESTRA_LLM__DEFAULT_MODEL={cfg.default_model}",
        "ORCHESTRA_LLM__TEMPERATURE=0.0",
        "ORCHESTRA_LLM__STREAM=true",
        "ORCHESTRA_LLM__ENABLE_THINKING=true",
        "",
        "# --- API Key Pool ---",
        f"ORCHESTRA_KEY_POOL__KEYS={keys_json}",
        f"ORCHESTRA_KEY_POOL__COOLDOWN_429_S={c.KEY_COOLDOWN_429_S}",
        f"ORCHESTRA_KEY_POOL__COOLDOWN_5XX_S={c.KEY_COOLDOWN_5XX_S}",
        f"ORCHESTRA_KEY_POOL__MAX_CONCURRENT_PER_KEY={c.MAX_CONCURRENT_PER_KEY}",
        "",
        "# --- Sandbox & Safety ---",
        "ORCHESTRA_SANDBOX__MODE=subprocess",
        "ORCHESTRA_SANDBOX__NETWORK=disabled",
        "ORCHESTRA_EXCHANGES__ENABLED=[]",
        "ORCHESTRA_TELEMETRY__REDACT_SECRETS=true",
        "",
        "# --- Telegram Bridge ---",
        f"ORCHESTRA_TELEGRAM__ENABLED={'true' if cfg.telegram_enabled else 'false'}",
        (
            f"ORCHESTRA_TELEGRAM__BOT_TOKEN={cfg.telegram_bot_token}"
            if cfg.telegram_bot_token
            else "# ORCHESTRA_TELEGRAM__BOT_TOKEN="
        ),
        f"ORCHESTRA_TELEGRAM__ALLOWED_USER_IDS={tg_ids_json}",
        "",
        "# --- Web Operator Console ---",
        "ORCHESTRA_WEB__ENABLED=true",
        f"ORCHESTRA_WEB__HOST={cfg.web_host}",
        f"ORCHESTRA_WEB__PORT={cfg.web_port}",
        (
            f"ORCHESTRA_WEB__ACCESS_TOKEN={cfg.web_access_token}"
            if cfg.web_access_token
            else "# ORCHESTRA_WEB__ACCESS_TOKEN="
        ),
        "",
    ]
    return "\n".join(lines)


def run_setup_wizard(
    env_path: Path = Path(".env"),
    *,
    input_fn: InputFn = builtins.input,
    output_fn: OutputFn = builtins.print,
) -> WizardCollectedConfig:
    """Run interactive setup, write `.env` atomically with `0600` perms, and validate it."""
    cfg = collect_wizard_inputs(input_fn=input_fn, output_fn=output_fn)
    if env_path.exists():
        backup_path = env_path.parent / f"{env_path.name}.bak"
        shutil.copyfile(env_path, backup_path)
        output_fn(f"\n[i] Предыдущий файл сохранён в резервную копию: {backup_path}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = render_env_content(cfg).replace("\r\n", "\n").replace("\r", "\n")
    tmp_path = env_path.parent / f".{env_path.name}.{uuid.uuid4().hex[:8]}.tmp"
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)
        handle.flush()
    tmp_path.replace(env_path)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            env_path.chmod(0o600)

    output_fn("\n" + "=" * 72)
    output_fn(f"  Готово! Конфигурация сохранена в: {env_path.resolve()}")
    try:
        from orchestra.config.settings import load_settings

        validated = load_settings(env_file=env_path)
        output_fn("  Проверка схемы: OK (Pydantic Settings)")
        output_fn(f"  Провайдер     : {validated.llm.provider}")
        output_fn(f"  Модель        : {validated.llm.default_model}")
        output_fn(f"  Ключей в пуле : {len(validated.key_pool.keys)}")
        output_fn(f"  Папка проектов: {validated.runtime_dir.resolve()}")
        output_fn(f"  Telegram      : {'включён' if validated.telegram.enabled else 'выключен'}")
    except ModuleNotFoundError:
        output_fn(f"  Провайдер     : {cfg.provider}")
        output_fn(f"  Модель        : {cfg.default_model}")
        output_fn(f"  Ключей в пуле : {len(cfg.api_keys)}")
        output_fn(f"  Папка проектов: {Path(cfg.runtime_dir).resolve()}")
        output_fn(f"  Telegram      : {'включён' if cfg.telegram_enabled else 'выключен'}")
    output_fn("=" * 72)
    return cfg
