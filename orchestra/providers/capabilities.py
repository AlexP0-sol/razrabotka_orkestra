"""Endpoint capability probing with disk caching and offline degradation (§13.2.2, CAP-01..03)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from orchestra.config.constants import CAPABILITIES_CACHE_TTL_S, PROBE_TIMEOUT_S
from orchestra.errors import BadRequest, ModelNotFoundError
from orchestra.providers.base import ProviderCapabilities

if TYPE_CHECKING:
    from pathlib import Path

REASONING_CANDIDATE_FIELDS: tuple[str, ...] = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "reasoning_text",
)


class ProbeTransport(Protocol):
    """Minimal transport interface used by CapabilityProber to inspect an OpenAI-compatible API."""

    async def list_models(self, *, api_key: str) -> list[str]:
        """Return model IDs advertised by `GET /models`."""
        ...

    async def micro_probe(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Run an 8-token streaming micro-request and return observed delta metadata."""
        ...


def _slugify_model(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model_id)


class CapabilityProber:
    """Probes and caches endpoint capabilities, degrading safely on network errors (CAP-01..03)."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        max_context_tokens: int = 131_072,
        default_rpm: int = 40,
        default_tpm: int = 120_000,
        ttl_s: int = CAPABILITIES_CACHE_TTL_S,
        timeout_s: float = PROBE_TIMEOUT_S,
    ) -> None:
        """Initialize CapabilityProber with cache location and fallback defaults."""
        self._cache_dir = cache_dir
        self._max_context_tokens = max_context_tokens
        self._default_rpm = default_rpm
        self._default_tpm = default_tpm
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, model_id: str) -> Path:
        return self._cache_dir / f"capabilities_{_slugify_model(model_id)}.json"

    def load_cached(
        self, model_id: str, *, now: datetime | None = None
    ) -> ProviderCapabilities | None:
        """Return cached ProviderCapabilities if present and within TTL."""
        path = self._cache_file(model_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            caps = ProviderCapabilities.model_validate(payload)
        except Exception:
            return None
        ref_now = now or datetime.now(UTC)
        age_s = (ref_now - caps.probed_at).total_seconds()
        if age_s > self._ttl_s:
            return None
        return caps

    def save_cached(self, caps: ProviderCapabilities) -> None:
        """Atomically persist ProviderCapabilities to the cache directory."""
        path = self._cache_file(caps.model_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(caps.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        tmp_path.replace(path)

    def assumed_capabilities(self, model_id: str) -> ProviderCapabilities:
        """Build degraded-mode capabilities when live probing cannot reach network (CAP-03)."""
        return ProviderCapabilities(
            model_id=model_id,
            exists=True,
            max_context_tokens=self._max_context_tokens,
            supports_reasoning_stream=True,
            reasoning_field_name="reasoning_content",
            supports_extra_body=True,
            supports_chat_template_kwargs=True,
            supports_reasoning_budget=True,
            min_temperature=0.0,
            rate_limit_rpm=self._default_rpm,
            rate_limit_tpm=self._default_tpm,
            capabilities_assumed=True,
            probed_at=datetime.now(UTC),
        )

    async def probe(
        self,
        transport: ProbeTransport,
        *,
        model_id: str,
        api_key: str,
        force_refresh: bool = False,
    ) -> ProviderCapabilities:
        """Execute full capability probe within `PROBE_TIMEOUT_S` (CAP-01..03).

        Raises:
            ModelNotFoundError: When `GET /models` succeeds and `model_id` is absent.
        """
        if not force_refresh:
            cached = self.load_cached(model_id)
            if cached is not None:
                return cached

        try:
            async with asyncio.timeout(self._timeout_s):
                caps = await self._run_probe_steps(transport, model_id=model_id, api_key=api_key)
        except ModelNotFoundError:
            raise
        except Exception:
            return self.assumed_capabilities(model_id)

        self.save_cached(caps)
        return caps

    async def _run_probe_steps(
        self,
        transport: ProbeTransport,
        *,
        model_id: str,
        api_key: str,
    ) -> ProviderCapabilities:
        models = await transport.list_models(api_key=api_key)
        if models and model_id not in models:
            sample = ", ".join(sorted(models)[:10])
            raise ModelNotFoundError(
                f"Модель '{model_id}' не найдена у провайдера. "
                f"Доступные модели (пример): {sample}. "
                "Проверьте ORCHESTRA_LLM__DEFAULT_MODEL в .env."
            )

        supports_extra_body = True
        supports_template_kwargs = True
        supports_reasoning_budget = True
        observed: dict[str, Any] = {}

        extra_payload: dict[str, Any] = {
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": 64,
        }
        try:
            observed = await transport.micro_probe(
                api_key=api_key,
                model=model_id,
                temperature=0.0,
                extra_body=extra_payload,
            )
            min_temp = 0.0
        except BadRequest as exc:
            msg = str(exc).lower()
            if "temperature" in msg:
                min_temp = await self._binary_search_min_temperature(
                    transport,
                    api_key=api_key,
                    model_id=model_id,
                )
                observed = await transport.micro_probe(
                    api_key=api_key,
                    model=model_id,
                    temperature=min_temp,
                    extra_body=None,
                )
                supports_extra_body = False
                supports_template_kwargs = False
                supports_reasoning_budget = False
            else:
                supports_extra_body = False
                supports_template_kwargs = False
                supports_reasoning_budget = False
                min_temp = 0.0
                observed = await transport.micro_probe(
                    api_key=api_key,
                    model=model_id,
                    temperature=0.0,
                    extra_body=None,
                )

        detected_field = str(observed.get("reasoning_field") or "")
        supports_reasoning = bool(detected_field)
        field_name = (
            detected_field if detected_field in REASONING_CANDIDATE_FIELDS else "reasoning_content"
        )

        return ProviderCapabilities(
            model_id=model_id,
            exists=True,
            max_context_tokens=self._max_context_tokens,
            supports_reasoning_stream=supports_reasoning,
            reasoning_field_name=field_name,
            supports_extra_body=supports_extra_body,
            supports_chat_template_kwargs=supports_template_kwargs,
            supports_reasoning_budget=supports_reasoning_budget,
            min_temperature=min_temp,
            rate_limit_rpm=self._default_rpm,
            rate_limit_tpm=self._default_tpm,
            capabilities_assumed=False,
            probed_at=datetime.now(UTC),
        )

    async def _binary_search_min_temperature(
        self,
        transport: ProbeTransport,
        *,
        api_key: str,
        model_id: str,
    ) -> float:
        """Find minimum accepted temperature in (0.0, 1.0] via 5-step binary search (CAP-01.4)."""
        low = 0.0
        high = 1.0
        best = 0.1
        for _ in range(5):
            mid = round((low + high) / 2, 3)
            if mid <= 0.0:
                mid = 0.01
            try:
                await transport.micro_probe(
                    api_key=api_key,
                    model=model_id,
                    temperature=mid,
                    extra_body=None,
                )
                best = mid
                high = mid
            except BadRequest:
                low = mid
        return best
