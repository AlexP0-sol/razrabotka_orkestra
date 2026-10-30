"""Unified DualStreamLLMRuntime with retries, continuation, JSON repair, and caching (§13.4)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import uuid
from functools import partial
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from orchestra.config import constants as c
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
    StructuredOutputError,
    TruncatedOutput,
)
from orchestra.inference.stream_router import (
    AssembledToolCall,
    DualStreamRouter,
    ReasoningLoopDetectedError,
    ThinkingAuditResult,
    audit_thinking_protocol,
    resolve_reasoning_budget,
)
from orchestra.inference.structured_output import (
    build_json_repair_messages,
    parse_structured_content,
)
from orchestra.providers.base import ChatMessage, ChatRequest, LLMProvider, ToolDefinition

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from orchestra.config.settings import Settings
    from orchestra.inference.key_pool import KeyLease, KeyPoolManager

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMResult(BaseModel, Generic[ModelT]):
    """Audited result of a DualStreamLLMRuntime call with separated streams (INV-07, INV-10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    content_text: str
    reasoning_text: str
    structured: ModelT | None = None
    tool_calls: tuple[AssembledToolCall, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    usage_estimated: bool = False
    key_id: str | None = None
    latency_ms: float = 0.0
    attempts: int = 1
    cached: bool = False
    thinking_audit: ThinkingAuditResult | None = None


class DualStreamLLMRuntime:
    """Single gateway through which all agents invoke LLMs (LLM-01..LLM-11, BAN-03)."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        settings: Settings,
        key_pool: KeyPoolManager | None = None,
        thinking_log_dir: Path | None = None,
        on_thinking_delta: Callable[[str, str], None] | None = None,
        on_content_delta: Callable[[str, str], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        """Initialize DualStreamLLMRuntime with semaphore, KeyPool, and optional disk cache."""
        self._provider = provider
        self._settings = settings
        self._key_pool = key_pool
        self._thinking_log_dir = thinking_log_dir
        self._on_thinking_delta = on_thinking_delta
        self._on_content_delta = on_content_delta
        self._sem = asyncio.Semaphore(settings.swarm.max_concurrent_llm_calls)
        self._rng = rng or random.Random(settings.llm.seed or c.DEFAULT_SEED)
        self._cache_dir = settings.paths.cache_dir / "llm_responses"
        if settings.llm.cache_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def complete(
        self,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        *,
        role: str = "developer",
        agent_id: str = "system",
        node_id: str | None = None,
        tools: tuple[ToolDefinition, ...] = (),
        response_schema: type[ModelT] | None = None,
        enable_thinking: bool | None = None,
        trivial: bool = False,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult[ModelT]:
        """Execute a complete LLM invocation with dual-stream routing, retries, and repair."""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        eff_temp = self._settings.llm.effective_temperature(temperature)
        use_thinking = (
            self._settings.llm.enable_thinking if enable_thinking is None else enable_thinking
        )
        max_tokens = max_output_tokens or self._settings.llm.max_output_tokens
        msg_tuple = tuple(messages)

        cache_key = self._compute_cache_key(
            messages=msg_tuple,
            tools=tools,
            temperature=eff_temp,
            response_schema=response_schema,
        )
        if cache_key is not None:
            cached_res = self._read_cache(cache_key, request_id=request_id, schema=response_schema)
            if cached_res is not None:
                return cached_res

        raw_result = await self._execute_with_retries_and_continuation(
            request_id=request_id,
            messages=msg_tuple,
            tools=tools,
            role=role,
            agent_id=agent_id,
            node_id=node_id,
            temperature=eff_temp,
            max_output_tokens=max_tokens,
            enable_thinking=use_thinking,
            trivial=trivial,
        )

        parsed_obj: ModelT | None = None
        if response_schema is not None:
            parsed_obj, raw_result = await self._validate_or_repair_structured(
                raw_result=raw_result,
                schema=response_schema,
                agent_id=agent_id,
                node_id=node_id,
            )

        audit: ThinkingAuditResult | None = None
        if raw_result.reasoning_text and self._rng.random() <= c.THINKING_AUDIT_PROBABILITY:
            audit = audit_thinking_protocol(raw_result.reasoning_text)

        final_res: LLMResult[ModelT] = LLMResult(
            request_id=raw_result.request_id,
            content_text=raw_result.content_text,
            reasoning_text=raw_result.reasoning_text,
            structured=parsed_obj,
            tool_calls=raw_result.tool_calls,
            prompt_tokens=raw_result.prompt_tokens,
            completion_tokens=raw_result.completion_tokens,
            reasoning_tokens=raw_result.reasoning_tokens,
            total_tokens=raw_result.total_tokens,
            usage_estimated=raw_result.usage_estimated,
            key_id=raw_result.key_id,
            latency_ms=raw_result.latency_ms,
            attempts=raw_result.attempts,
            cached=False,
            thinking_audit=audit,
        )
        if cache_key is not None:
            self._write_cache(cache_key, final_res)
        return final_res

    async def _execute_with_retries_and_continuation(
        self,
        *,
        request_id: str,
        messages: tuple[ChatMessage, ...],
        tools: tuple[ToolDefinition, ...],
        role: str,
        agent_id: str,
        node_id: str | None,
        temperature: float,
        max_output_tokens: int,
        enable_thinking: bool,
        trivial: bool,
    ) -> LLMResult[Any]:
        start_t = time.monotonic()
        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        cur_messages = messages
        continuations = 0
        total_p = 0
        total_c = 0
        total_r = 0
        any_estimated = False
        total_attempts = 0
        last_key_id: str | None = None
        loop_reprompted = False

        while True:
            extra_body = self._build_extra_body(
                role=role,
                enable_thinking=enable_thinking,
                trivial=trivial,
            )
            req = ChatRequest(
                request_id=request_id,
                model=self._settings.llm.default_model,
                messages=cur_messages,
                tools=tools,
                temperature=temperature,
                top_p=self._settings.llm.top_p,
                seed=self._settings.llm.seed,
                max_output_tokens=max_output_tokens,
                extra_body=extra_body,
                timeout_s=self._settings.llm.request_timeout_s,
                role=role,
                node_id=node_id,
                agent_id=agent_id,
            )
            try:
                step_out, key_id, attempts_used = await self._attempt_single_stream(req)
                total_attempts += attempts_used
                last_key_id = key_id
                accumulated_content.append(step_out.content_text)
                accumulated_reasoning.append(step_out.reasoning_text)
                total_p += step_out.prompt_tokens
                total_c += step_out.completion_tokens
                total_r += step_out.reasoning_tokens
                any_estimated = any_estimated or step_out.usage_estimated
                latency = round((time.monotonic() - start_t) * 1000, 2)
                return LLMResult(
                    request_id=request_id,
                    content_text="".join(accumulated_content),
                    reasoning_text="".join(accumulated_reasoning),
                    tool_calls=step_out.tool_calls,
                    prompt_tokens=total_p,
                    completion_tokens=total_c,
                    reasoning_tokens=total_r,
                    total_tokens=total_p + total_c + total_r,
                    usage_estimated=any_estimated,
                    key_id=last_key_id,
                    latency_ms=latency,
                    attempts=total_attempts,
                )
            except ReasoningLoopDetectedError:
                if loop_reprompted:
                    raise
                loop_reprompted = True
                cur_messages = (
                    *cur_messages,
                    ChatMessage(
                        role="user",
                        content="STOP REPEATING REASONING. Output concise final answer directly.",
                    ),
                )
            except TruncatedOutput as trunc_exc:
                if continuations >= c.MAX_CONTINUATIONS or not trunc_exc.partial_content:
                    raise
                continuations += 1
                accumulated_content.append(trunc_exc.partial_content)
                cur_messages = (
                    *messages,
                    ChatMessage(role="assistant", content="".join(accumulated_content)),
                    ChatMessage(
                        role="user",
                        content=(
                            "Continue EXACTLY from the next character after the assistant prefix "
                            "above without repeating any prior text."
                        ),
                    ),
                )

    async def _attempt_single_stream(
        self,
        req: ChatRequest,
    ) -> tuple[Any, str | None, int]:
        max_tries = self._settings.swarm.max_retries_per_node + 1
        last_exc: ProviderError | None = None
        prompt_joined = "\n".join(m.content for m in req.messages)
        est_tokens = await self._provider.count_tokens(prompt_joined)

        for attempt in range(1, max_tries + 1):
            lease: KeyLease | None = None
            if self._key_pool is not None and self._key_pool.size > 0:
                lease = await self._key_pool.acquire(
                    agent_id=req.agent_id,
                    estimated_tokens=est_tokens,
                    timeout_s=req.timeout_s,
                )
            elif self._provider.name != "mock":
                raise AuthFailed("No KeyPool configured for live LLM provider")

            t_cb = self._on_thinking_delta
            c_cb = self._on_content_delta
            router = DualStreamRouter(
                thinking_log_dir=self._thinking_log_dir,
                on_thinking_delta=(partial(t_cb, req.agent_id) if t_cb is not None else None),
                on_content_delta=(partial(c_cb, req.agent_id) if c_cb is not None else None),
            )
            raw_key = lease.raw_key.get_secret_value() if lease is not None else None
            key_id = lease.key_id if lease is not None else None

            try:
                async with self._sem:
                    routed = await router.consume(
                        self._provider.stream_chat(req, api_key=raw_key),
                        request_id=req.request_id,
                        prompt_text=prompt_joined,
                    )
                if lease is not None and self._key_pool is not None:
                    await self._key_pool.release(
                        lease,
                        outcome="ok",
                        prompt_tokens=routed.prompt_tokens,
                        completion_tokens=routed.completion_tokens,
                        reasoning_tokens=routed.reasoning_tokens,
                    )
                return routed, key_id, attempt
            except (
                ReasoningLoopDetectedError,
                TruncatedOutput,
                BadRequest,
                ContextOverflow,
                ContentFiltered,
            ):
                if lease is not None and self._key_pool is not None:
                    await self._key_pool.release(lease, outcome="ok")
                raise
            except AuthFailed as exc:
                if lease is not None and self._key_pool is not None:
                    await self._key_pool.release(lease, outcome="auth_failed")
                last_exc = exc
                if self._key_pool is None or self._key_pool.size <= 1:
                    raise
            except RateLimited as exc:
                if lease is not None and self._key_pool is not None:
                    await self._key_pool.release(
                        lease,
                        outcome="rate_limit",
                        retry_after_s=exc.retry_after_s,
                    )
                last_exc = exc
            except (ProviderUnavailable, RequestTimeout, StreamInterrupted) as exc:
                if lease is not None and self._key_pool is not None:
                    await self._key_pool.release(lease, outcome="server_error")
                last_exc = exc

            if attempt < max_tries:
                cap = min(c.RETRY_MAX_DELAY_S, c.RETRY_BASE_S * (2 ** (attempt - 1)))
                # When multiple keys exist in pool, failover quickly; otherwise full jitter backoff
                delay = (
                    0.02
                    if (self._key_pool is not None and self._key_pool.size > 1)
                    else self._rng.uniform(0.01, max(0.02, cap))
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def _validate_or_repair_structured(
        self,
        *,
        raw_result: LLMResult[Any],
        schema: type[ModelT],
        agent_id: str,
        node_id: str | None,
    ) -> tuple[ModelT, LLMResult[Any]]:
        try:
            parsed = parse_structured_content(raw_result.content_text, schema)
            return parsed, raw_result
        except StructuredOutputError as first_err:
            repair_msgs = build_json_repair_messages(
                broken_content=raw_result.content_text,
                validation_error=str(first_err),
                schema=schema,
            )
            repaired_res = await self._execute_with_retries_and_continuation(
                request_id=f"{raw_result.request_id}_repair",
                messages=repair_msgs,
                tools=(),
                role="trivial",
                agent_id=agent_id,
                node_id=node_id,
                temperature=0.0,
                max_output_tokens=2048,
                enable_thinking=False,
                trivial=True,
            )
            parsed = parse_structured_content(repaired_res.content_text, schema)
            merged: LLMResult[Any] = LLMResult(
                request_id=raw_result.request_id,
                content_text=repaired_res.content_text,
                reasoning_text=raw_result.reasoning_text,
                tool_calls=raw_result.tool_calls,
                prompt_tokens=raw_result.prompt_tokens + repaired_res.prompt_tokens,
                completion_tokens=raw_result.completion_tokens + repaired_res.completion_tokens,
                reasoning_tokens=raw_result.reasoning_tokens + repaired_res.reasoning_tokens,
                total_tokens=raw_result.total_tokens + repaired_res.total_tokens,
                usage_estimated=raw_result.usage_estimated or repaired_res.usage_estimated,
                key_id=repaired_res.key_id or raw_result.key_id,
                latency_ms=round(raw_result.latency_ms + repaired_res.latency_ms, 2),
                attempts=raw_result.attempts + repaired_res.attempts,
            )
            return parsed, merged

    def _build_extra_body(
        self,
        *,
        role: str,
        enable_thinking: bool,
        trivial: bool,
    ) -> dict[str, Any]:
        budget = (
            resolve_reasoning_budget(role, self._settings.reasoning, trivial=trivial)
            if enable_thinking
            else 0
        )
        payload: dict[str, Any] = {
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if enable_thinking and budget > 0:
            payload["reasoning_budget"] = budget
        return payload

    def _compute_cache_key(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        tools: tuple[ToolDefinition, ...],
        temperature: float,
        response_schema: type[BaseModel] | None,
    ) -> str | None:
        if not self._settings.llm.cache_enabled or temperature != 0.0:
            return None
        blob = json.dumps(
            {
                "model": self._settings.llm.default_model,
                "seed": self._settings.llm.seed,
                "temperature": temperature,
                "messages": [m.model_dump() for m in messages],
                "tools": [t.model_dump() for t in tools],
                "schema": response_schema.__name__ if response_schema else None,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _read_cache(
        self,
        cache_key: str,
        *,
        request_id: str,
        schema: type[ModelT] | None,
    ) -> LLMResult[ModelT] | None:
        path = self._cache_dir / f"{cache_key}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            content_text = str(data["content_text"])
            structured = parse_structured_content(content_text, schema) if schema else None
            return LLMResult(
                request_id=request_id,
                content_text=content_text,
                reasoning_text=str(data.get("reasoning_text", "")),
                structured=structured,
                cached=True,
            )
        except Exception:
            return None

    def _write_cache(self, cache_key: str, result: LLMResult[Any]) -> None:
        path = self._cache_dir / f"{cache_key}.json"
        tmp = path.with_suffix(".json.tmp")
        payload = {
            "content_text": result.content_text,
            "reasoning_text": result.reasoning_text,
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(path)
