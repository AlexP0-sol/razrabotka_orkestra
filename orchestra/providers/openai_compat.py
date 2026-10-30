"""OpenAI-compatible streaming provider (NVIDIA Build default) (§13.2.3, OAI-01..08)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx
import openai
from openai import AsyncOpenAI

from orchestra.errors import (
    AuthFailed,
    BadRequest,
    ContentFiltered,
    ContextOverflow,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    RequestTimeout,
    StreamInterrupted,
    TruncatedOutput,
)
from orchestra.providers.base import (
    ChatRequest,
    ContentDelta,
    FinishDelta,
    ProviderCapabilities,
    ReasoningDelta,
    StreamDelta,
    ToolCallDelta,
    UsageDelta,
    compute_key_fingerprint,
)
from orchestra.providers.capabilities import REASONING_CANDIDATE_FIELDS, CapabilityProber
from orchestra.providers.token_counter import TokenCounter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class OpenAICompatProvider:
    """Async OpenAI-compatible provider with per-key HTTP keep-alive client pool (OAI-01..08)."""

    name: str = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        prober: CapabilityProber,
        request_timeout_s: float = 180.0,
        token_counter: TokenCounter | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        """Initialize OpenAICompatProvider."""
        self._base_url = base_url
        self._default_model = default_model
        self._prober = prober
        self._request_timeout_s = request_timeout_s
        self._token_counter = token_counter or TokenCounter()
        self._http_client_factory = http_client_factory
        self._clients: dict[str, AsyncOpenAI] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def _get_client(self, api_key: str) -> tuple[str, AsyncOpenAI]:
        """Return `(key_id, AsyncOpenAI)` reusing keep-alive connections per key (OAI-02)."""
        key_id = compute_key_fingerprint(api_key)
        client = self._clients.get(key_id)
        if client is None:
            http_client = (
                self._http_client_factory()
                if self._http_client_factory is not None
                else httpx.AsyncClient(timeout=self._request_timeout_s)
            )
            client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=api_key,
                timeout=self._request_timeout_s,
                max_retries=0,
                http_client=cast("Any", http_client),
            )
            self._clients[key_id] = client
        return key_id, client

    async def evict_key(self, key_id: str) -> None:
        """Close and evict a cached client when a key is removed from KeyPool (OAI-02)."""
        client = self._clients.pop(key_id, None)
        if client is not None:
            await client.close()

    async def list_models(self, *, api_key: str) -> list[str]:
        """Query `GET /models` via AsyncOpenAI for capability probing."""
        _, client = self._get_client(api_key)
        try:
            page = await client.models.list()
            return [item.id for item in page.data]
        except Exception as exc:
            raise _classify_openai_exception(exc) from exc

    async def micro_probe(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Execute an 8-token streaming micro-request to detect reasoning fields and params."""
        _, client = self._get_client(api_key)
        detected_reasoning_field: str | None = None
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply OK"}],
            "max_tokens": 8,
            "temperature": temperature,
            "stream": True,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = choices[0].delta
                field_name, text = _extract_reasoning_from_delta(delta, preferred=None)
                if text and field_name:
                    detected_reasoning_field = field_name
        except Exception as exc:
            raise _classify_openai_exception(exc) from exc

        return {"reasoning_field": detected_reasoning_field}

    async def probe(self, *, api_key: str | None = None) -> ProviderCapabilities:
        """Probe capabilities for `default_model` or return degraded assumptions if no key."""
        if not api_key:
            caps = self._prober.assumed_capabilities(self._default_model)
            self._capabilities[self._default_model] = caps
            return caps
        caps = await self._prober.probe(self, model_id=self._default_model, api_key=api_key)
        self._capabilities[self._default_model] = caps
        return caps

    def set_capabilities(self, caps: ProviderCapabilities) -> None:
        """Register pre-probed capabilities for a model."""
        self._capabilities[caps.model_id] = caps

    def get_capabilities(self, model_id: str) -> ProviderCapabilities:
        """Return known capabilities for `model_id` or safe defaults."""
        return self._capabilities.get(model_id) or self._prober.assumed_capabilities(model_id)

    async def stream_chat(
        self,
        req: ChatRequest,
        *,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Stream normalized deltas with strict reasoning/content separation (OAI-03..06)."""
        if not api_key:
            raise AuthFailed("API key is required for OpenAICompatProvider.stream_chat")

        caps = self.get_capabilities(req.model)
        _, client = self._get_client(api_key)
        kwargs = _build_completion_kwargs(req, caps)

        partial_content: list[str] = []
        partial_reasoning: list[str] = []
        finish_reason: str | None = None

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    r_tokens = _extract_reasoning_tokens(usage)
                    yield UsageDelta(
                        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                        reasoning_tokens=r_tokens,
                        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                    )

                choices = getattr(chunk, "choices", None)
                if not choices:
                    # Heartbeat chunk from provider (OAI-04)
                    continue

                choice = choices[0]
                delta = choice.delta
                _, r_text = _extract_reasoning_from_delta(
                    delta, preferred=caps.reasoning_field_name
                )
                if r_text:
                    partial_reasoning.append(r_text)
                    yield ReasoningDelta(text=r_text)

                c_text = getattr(delta, "content", None)
                if c_text:
                    partial_content.append(c_text)
                    yield ContentDelta(text=c_text)

                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        fn = getattr(tc, "function", None)
                        yield ToolCallDelta(
                            index=int(getattr(tc, "index", 0) or 0),
                            id=getattr(tc, "id", None),
                            name=getattr(fn, "name", None) if fn else None,
                            arguments_chunk=str(getattr(fn, "arguments", "") or "") if fn else "",
                        )

                if choice.finish_reason:
                    finish_reason = str(choice.finish_reason)
        except Exception as exc:
            classified = _classify_openai_exception(exc)
            if partial_content or partial_reasoning:
                raise StreamInterrupted(
                    f"Stream interrupted after partial output: {classified}",
                    partial_content="".join(partial_content),
                    partial_reasoning="".join(partial_reasoning),
                ) from exc
            raise classified from exc

        if finish_reason == "content_filter":
            raise ContentFiltered("Generation blocked by provider content_filter")
        if finish_reason == "length":
            raise TruncatedOutput(
                "Generation stopped due to max_tokens length limit",
                partial_content="".join(partial_content),
            )
        yield FinishDelta(reason=finish_reason or "stop")

    async def count_tokens(self, text: str) -> int:
        """Count tokens using the configured TokenCounter."""
        return await self._token_counter.count_async(text)

    async def close(self) -> None:
        """Close all cached AsyncOpenAI HTTP clients."""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()


def _build_completion_kwargs(req: ChatRequest, caps: ProviderCapabilities) -> dict[str, Any]:
    messages_payload: list[dict[str, Any]] = []
    for msg in req.messages:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.name is not None:
            entry["name"] = msg.name
        if msg.tool_call_id is not None:
            entry["tool_call_id"] = msg.tool_call_id
        messages_payload.append(entry)

    effective_temp = max(req.temperature, caps.min_temperature)
    kwargs: dict[str, Any] = {
        "model": req.model,
        "messages": messages_payload,
        "max_tokens": req.max_output_tokens,
        "temperature": effective_temp,
        "timeout": req.timeout_s,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if req.top_p is not None:
        kwargs["top_p"] = req.top_p
    if req.seed is not None and caps.supports_seed:
        kwargs["seed"] = req.seed
    if req.tools and caps.supports_tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in req.tools
        ]

    if req.extra_body and caps.supports_extra_body:
        filtered_extra: dict[str, Any] = {}
        for key, value in req.extra_body.items():
            if key == "chat_template_kwargs" and not caps.supports_chat_template_kwargs:
                continue
            if key == "reasoning_budget" and not caps.supports_reasoning_budget:
                continue
            filtered_extra[key] = value
        if filtered_extra:
            kwargs["extra_body"] = filtered_extra

    return kwargs


def _extract_reasoning_from_delta(
    delta: Any,
    *,
    preferred: str | None,
) -> tuple[str | None, str | None]:
    """Defensive extraction of non-standard reasoning fields from SDK delta (OAI-03)."""
    fields_order = (
        (preferred, *(f for f in REASONING_CANDIDATE_FIELDS if f != preferred))
        if preferred
        else REASONING_CANDIDATE_FIELDS
    )
    extra = getattr(delta, "model_extra", None) or {}
    for field in fields_order:
        if not field:
            continue
        direct = getattr(delta, field, None)
        if isinstance(direct, str) and direct:
            return field, direct
        if isinstance(extra, dict):
            from_extra = extra.get(field)
            if isinstance(from_extra, str) and from_extra:
                return field, from_extra
    return None, None


def _extract_reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        val = getattr(details, "reasoning_tokens", None)
        if isinstance(val, int):
            return val
    return 0


def _classify_openai_exception(exc: Exception) -> ProviderError:
    """Map OpenAI SDK / HTTP exceptions to Orchestra's typed error matrix (OAI-06)."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, openai.RateLimitError):
        retry_after = _parse_retry_after(getattr(exc, "response", None))
        return RateLimited(str(exc), retry_after_s=retry_after)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AuthFailed(str(exc))
    if isinstance(exc, openai.BadRequestError):
        lower = str(exc).lower()
        if any(tok in lower for tok in ("context_length", "maximum context", "too many tokens")):
            return ContextOverflow(str(exc))
        return BadRequest(str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return RequestTimeout(str(exc))
    if isinstance(exc, openai.InternalServerError):
        return ProviderUnavailable(str(exc))
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 0)
        if status == 429:
            return RateLimited(str(exc), retry_after_s=_parse_retry_after(exc.response))
        if status in (401, 403):
            return AuthFailed(str(exc))
        if status == 400:
            lower = str(exc).lower()
            if "context" in lower and "token" in lower:
                return ContextOverflow(str(exc))
            return BadRequest(str(exc))
        if 500 <= status <= 599:
            return ProviderUnavailable(str(exc))
    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailable(str(exc))
    return ProviderError(str(exc))


def _parse_retry_after(response: Any) -> int | None:
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return None
