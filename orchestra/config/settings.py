"""Centralized L0 configuration using Pydantic Settings v2 (§13.1, CFG-01..CFG-08).

Reads environment variables prefixed with `ORCHESTRA_` (nested groups delimited by `__`),
optional `.env` files, and optional profile TOML files (`dev | ci | prod`).
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from orchestra.config import constants as c
from orchestra.errors import ConfigurationError


class LLMSettings(BaseModel):
    """LLM provider and generation settings."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai_compat", "mock"] = "openai_compat"
    base_url: HttpUrl = Field(default_factory=lambda: HttpUrl(c.DEFAULT_NVIDIA_BASE_URL))
    default_model: str = c.DEFAULT_MODEL_ID
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    min_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    seed: int | None = c.DEFAULT_SEED
    max_output_tokens: int = Field(default=c.DEFAULT_MAX_OUTPUT_TOKENS, ge=16, le=262_144)
    request_timeout_s: float = Field(default=c.DEFAULT_REQUEST_TIMEOUT_S, gt=0.0, le=3_600.0)
    stream: bool = True
    enable_thinking: bool = True
    cache_enabled: bool = True

    def effective_temperature(self, requested: float | None = None) -> float:
        """Return temperature clamped to `min_temperature` (C-03, CFG-05)."""
        target = self.temperature if requested is None else requested
        return max(target, self.min_temperature)


class KeyPoolSettings(BaseModel):
    """API key pool lease, rate-limit, and cooldown settings."""

    model_config = ConfigDict(extra="forbid")

    keys: list[SecretStr] = Field(default_factory=list)
    cooldown_429_s: int = Field(default=c.KEY_COOLDOWN_429_S, ge=1)
    cooldown_5xx_s: int = Field(default=c.KEY_COOLDOWN_5XX_S, ge=1)
    max_consecutive_failures: int = Field(default=c.KEY_MAX_CONSECUTIVE_FAILURES, ge=1)
    probe_on_start: bool = True
    exclusive_lease: bool = False
    max_concurrent_per_key: int = Field(default=c.MAX_CONCURRENT_PER_KEY, ge=1, le=64)
    default_rpm: int = Field(default=40, ge=1)
    default_tpm: int = Field(default=120_000, ge=100)

    @field_validator("keys", mode="before")
    @classmethod
    def _parse_comma_separated_keys(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped == "[]":
                return []
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value


class SwarmSettings(BaseModel):
    """Swarm parallelism, retry, and approval settings."""

    model_config = ConfigDict(extra="forbid")

    max_concurrent_agents: int = Field(default=c.MAX_CONCURRENT_AGENTS, ge=1, le=64)
    max_concurrent_llm_calls: int = Field(default=c.MAX_CONCURRENT_LLM_CALLS, ge=1, le=128)
    max_retries_per_node: int = Field(default=c.MAX_RETRIES, ge=0, le=10)
    max_agents_hard_cap: int = Field(default=c.MAX_AGENTS_HARD_CAP, ge=2, le=50)
    require_team_approval: bool = True


class GenerationSettings(BaseModel):
    """Code generation chunk size bounds (C-05)."""

    model_config = ConfigDict(extra="forbid")

    max_chunk_lines: int = Field(default=c.MAX_CHUNK_LINES, ge=50, le=1_000)
    recommended_chunk_lines: int = Field(default=c.RECOMMENDED_CHUNK_LINES, ge=30, le=1_000)


class MapSettings(BaseModel):
    """Mental Map snapshot cadence and lease timing."""

    model_config = ConfigDict(extra="forbid")

    snapshot_every_events: int = Field(default=c.SNAPSHOT_EVERY_EVENTS, ge=1)
    snapshot_backups: int = Field(default=c.SNAPSHOT_BACKUPS, ge=1, le=50)
    lease_ttl_s: int = Field(default=c.LEASE_TTL_S, ge=10)
    lease_heartbeat_s: int = Field(default=c.LEASE_HEARTBEAT_S, ge=1)


class SandboxSettings(BaseModel):
    """Execution sandbox isolation and resource cap settings."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["subprocess", "docker"] = "subprocess"
    timeout_s: int = Field(default=c.SANDBOX_TIMEOUT_S, ge=1, le=3_600)
    memory_limit_mb: int = Field(default=c.SANDBOX_MEMORY_LIMIT_MB, ge=128, le=65_536)
    cpu_limit: float = Field(default=c.SANDBOX_CPU_LIMIT, gt=0.0, le=64.0)
    network: Literal["disabled", "allowlist"] = "disabled"
    docker_image: str = "python:3.12-slim"


class TelegramSettings(BaseModel):
    """Telegram Human-in-the-Loop bridge configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    bot_token: SecretStr | None = None
    allowed_user_ids: list[int] = Field(default_factory=list)
    question_timeout_s: int = Field(default=c.TG_QUESTION_TIMEOUT_S, ge=5)
    interview_timeout_s: int = Field(default=c.TG_INTERVIEW_TIMEOUT_S, ge=10)
    default_action: Literal["pause_node", "apply_recommendation"] = "pause_node"


class WebSettings(BaseModel):
    """FastAPI + WebSocket + offline vanilla dashboard configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65_535)
    access_token: SecretStr | None = None
    frontend: Literal["vanilla"] = "vanilla"
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])


class PlatformSettings(BaseModel):
    """Cross-platform and Windows filesystem settings (§13.21)."""

    model_config = ConfigDict(extra="forbid")

    atomic_replace_retries: int = Field(default=c.ATOMIC_REPLACE_RETRIES, ge=1, le=20)
    path_length_warn: int = Field(default=c.PATH_LENGTH_WARN, ge=100, le=32_767)
    enforce_lf: bool = True


class ExchangesSettings(BaseModel):
    """Multi-exchange registry configuration (§38.19, DP-78, DP-88)."""

    model_config = ConfigDict(extra="forbid")

    registry_path: Path = Path("exchanges.yaml")
    enabled: list[str] = Field(default_factory=list)
    align_tolerance_ms: int = Field(default=c.ALIGN_TOLERANCE_MS, ge=1, le=60_000)


class BudgetsSettings(BaseModel):
    """Token and wall-clock budgets for nodes and projects."""

    model_config = ConfigDict(extra="forbid")

    project_token_limit: int | None = Field(default=None, ge=1_000)
    project_wallclock_s: int | None = Field(default=None, ge=10)
    node_token_limit: int = Field(default=c.NODE_TOKEN_LIMIT, ge=1_000)
    no_progress_alert_s: int = Field(default=c.NO_PROGRESS_ALERT_S, ge=10)


class ReasoningSettings(BaseModel):
    """Per-role reasoning token budgets (§15.3)."""

    model_config = ConfigDict(extra="forbid")

    architect_budget: int = Field(default=c.REASONING_ARCHITECT, ge=0, le=131_072)
    qa_budget: int = Field(default=c.REASONING_QA, ge=0, le=131_072)
    developer_budget: int = Field(default=c.REASONING_DEV, ge=0, le=131_072)
    ui_budget: int = Field(default=c.REASONING_UI, ge=0, le=131_072)
    trivial_budget: int = Field(default=c.REASONING_TRIVIAL, ge=0, le=131_072)


class TelemetrySettings(BaseModel):
    """Logging level and secret redaction settings."""

    model_config = ConfigDict(extra="forbid")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    redact_secrets: bool = True
    json_logs: bool = True


class PathsSettings(BaseModel):
    """Computed runtime directory layout (ST-02, ST-03, CFG-04)."""

    model_config = ConfigDict(extra="forbid")

    runtime_dir: Path = Path("./runtime")

    @property
    def projects_dir(self) -> Path:
        """Directory containing per-project workspaces and snapshots."""
        return self.runtime_dir / "projects"

    @property
    def talents_dir(self) -> Path:
        """Directory storing persistent `/agent_talents/` profiles and diaries."""
        return self.runtime_dir / "agent_talents"

    @property
    def skills_dir(self) -> Path:
        """Directory storing executable `/skills/` modules and manifest."""
        return self.runtime_dir / "skills"

    @property
    def cache_dir(self) -> Path:
        """Directory storing LLM response cache, capability probes, and key pool state."""
        return self.runtime_dir / "cache"

    @property
    def docs_cache_dir(self) -> Path:
        """Directory storing offline documentation snippets for research."""
        return self.runtime_dir / "docs_cache"

    def ensure_created(self) -> None:
        """Idempotently create all required runtime subdirectories (CFG-04)."""
        for directory in (
            self.runtime_dir,
            self.projects_dir,
            self.talents_dir,
            self.skills_dir,
            self.cache_dir,
            self.docs_cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Root application settings validated fail-fast at startup (CFG-01..CFG-08)."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: Literal["dev", "ci", "prod"] = "dev"
    runtime_dir: Path = Path("./runtime")
    llm: LLMSettings = Field(default_factory=LLMSettings)
    key_pool: KeyPoolSettings = Field(default_factory=KeyPoolSettings)
    swarm: SwarmSettings = Field(default_factory=SwarmSettings)
    gen: GenerationSettings = Field(default_factory=GenerationSettings)
    map: MapSettings = Field(default_factory=MapSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    platform: PlatformSettings = Field(default_factory=PlatformSettings)
    exchanges: ExchangesSettings = Field(default_factory=ExchangesSettings)
    budgets: BudgetsSettings = Field(default_factory=BudgetsSettings)
    reasoning: ReasoningSettings = Field(default_factory=ReasoningSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)

    @property
    def paths(self) -> PathsSettings:
        """Return computed runtime paths helper."""
        return PathsSettings(runtime_dir=self.runtime_dir)

    def model_post_init(self, __context: Any) -> None:
        """Ensure runtime directories exist and cross-field invariants hold."""
        if self.gen.recommended_chunk_lines > self.gen.max_chunk_lines:
            raise ConfigurationError(
                "Ошибка конфигурации ORCHESTRA_GEN: recommended_chunk_lines "
                f"({self.gen.recommended_chunk_lines}) не может превышать "
                f"max_chunk_lines ({self.gen.max_chunk_lines})."
            )
        if self.map.lease_heartbeat_s >= self.map.lease_ttl_s:
            raise ConfigurationError(
                "Ошибка конфигурации ORCHESTRA_MAP: lease_heartbeat_s "
                f"({self.map.lease_heartbeat_s}) должен быть строго меньше "
                f"lease_ttl_s ({self.map.lease_ttl_s})."
            )
        if self.profile == "prod" and not self.telemetry.redact_secrets:
            raise ConfigurationError(
                "Ошибка конфигурации ORCHESTRA_TELEMETRY__REDACT_SECRETS: "
                "в профиле 'prod' маскирование секретов обязательно (должно быть true)."
            )
        self.paths.ensure_created()

    def validate_for_live_inference(self) -> None:
        """Fail-fast if `openai_compat` provider is selected without any API keys (§13.1)."""
        if self.llm.provider == "openai_compat" and not self.key_pool.keys:
            raise ConfigurationError(
                "Ошибка конфигурации ORCHESTRA_KEY_POOL__KEYS: пул API-ключей пуст при "
                "ORCHESTRA_LLM__PROVIDER='openai_compat'. Укажите ключи в .env или переключите "
                "провайдер на 'mock' для локального демо и тестов."
            )

    def dump_public(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary with all secrets masked (CFG-07)."""
        data = self.model_dump(mode="json")
        data["key_pool"]["keys"] = [f"***REDACTED_{i}***" for i in range(len(self.key_pool.keys))]
        if self.telegram.bot_token is not None:
            data["telegram"]["bot_token"] = "***REDACTED***"
        if self.web.access_token is not None:
            data["web"]["access_token"] = "***REDACTED***"
        return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(
    *,
    overrides: dict[str, Any] | None = None,
    profile_dir: Path | None = None,
    env_file: Path | str | None = ".env",
) -> Settings:
    """Load Settings applying profile TOML and explicit overrides with Russian error messages.

    Args:
        overrides: Explicit dictionary overrides taking highest precedence.
        profile_dir: Optional directory containing `{profile}.toml` files (CFG-08).
        env_file: Path to `.env` file or `None` to disable file reading.

    Returns:
        Validated `Settings` instance with initialized runtime directories.

    Raises:
        ConfigurationError: With a clear Russian diagnostic message on invalid values (CFG-03).
    """
    profile_name = (overrides or {}).get("profile") or os.environ.get("ORCHESTRA_PROFILE") or "dev"
    merged_init: dict[str, Any] = {}
    search_dir = profile_dir or Path("config/profiles")
    profile_file = search_dir / f"{profile_name}.toml"
    if profile_file.is_file():
        try:
            merged_init = tomllib.loads(profile_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(
                f"Ошибка чтения профиля конфигурации '{profile_file}': {exc}"
            ) from exc

    if overrides:
        merged_init = _deep_merge(merged_init, overrides)

    try:
        init_kwargs: dict[str, Any] = {"_env_file": env_file, **merged_init}
        return Settings(**init_kwargs)
    except ValidationError as exc:
        details = "; ".join(
            f"ORCHESTRA_{'__'.join(str(p).upper() for p in err['loc'])}: {err['msg']} "
            f"(получено={err.get('input')!r})"
            for err in exc.errors()
        )
        raise ConfigurationError(
            f"Недопустимое значение конфигурации ORCHESTRA: {details}"
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance (QS-07)."""
    return load_settings()
