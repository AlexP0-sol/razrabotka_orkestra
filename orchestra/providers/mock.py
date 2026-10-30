"""Deterministic offline MockProvider for unit, e2e, and chaos tests (§13.2.4, MCK-01..04)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from orchestra.errors import (
    AuthFailed,
    BadRequest,
    ContentFiltered,
    ContextOverflow,
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
from orchestra.providers.token_counter import TokenCounter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


FaultType = Literal[
    "rate_limit",
    "auth_failed",
    "bad_request",
    "context_overflow",
    "server_error",
    "timeout",
    "stream_interrupted",
    "truncated_length",
    "content_filter",
]


class MockToolCallSpec(BaseModel):
    """Tool call emitted by a MockStep."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = "call_mock_1"
    name: str
    arguments: str


class MockStep(BaseModel):
    """Single deterministic response or injected fault rule for MockProvider (MCK-02, MCK-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str | None = None
    node_id: str | None = None
    prompt_regex: str | None = None
    key_id: str | None = None
    reasoning_chunks: tuple[str, ...] = (
        "1 BOUNDARY ANALYSIS: VERIFIED. ",
        "2 DIARY CHECK: CLEAN. 3 VULN: CHECKED. 4 SKELETON: MATCHED. 5 SELF-CRITIQUE: OPTIMAL.",
    )
    content_chunks: tuple[str, ...] = ('{"status": "ok"}',)
    tool_calls: tuple[MockToolCallSpec, ...] = ()
    fault: FaultType | None = None
    retry_after_s: int | None = None
    finish_reason: str = "stop"
    max_uses: int | None = None


class MockScenario(BaseModel):
    """Collection of ordered MockStep rules loaded from memory or a YAML fixture file."""

    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    default_content: str = '{"status": "ok"}'
    default_reasoning: str = (
        "1 BOUNDARY ANALYSIS. 2 DIARY CHECK. 3 VULNERABILITY SEARCH. "
        "4 ARCHITECTURAL SKELETON. 5 SELF-CRITIQUE."
    )
    steps: list[MockStep] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> MockScenario:
        """Load a MockScenario from a YAML fixture file (MCK-02)."""
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Scenario YAML at {path} must be a mapping")
        return cls.model_validate(raw)


class RecordedCall(BaseModel):
    """Audit record of a request received by MockProvider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ChatRequest
    key_id: str | None


class MockProvider:
    """Zero-network deterministic LLMProvider supporting full fault injection (MCK-01..04)."""

    name: str = "mock"

    def __init__(
        self,
        scenario: MockScenario | None = None,
        *,
        capabilities: ProviderCapabilities | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize MockProvider with an optional scenario and capabilities."""
        self._scenario = scenario or MockScenario()
        self._capabilities = capabilities or ProviderCapabilities(
            model_id="mock/deterministic-v1",
            exists=True,
            max_context_tokens=131_072,
            supports_reasoning_stream=True,
        )
        self._token_counter = token_counter or TokenCounter()
        self._step_uses: dict[int, int] = {}
        self.calls: list[RecordedCall] = []
        self.closed: bool = False

    def add_step(self, step: MockStep) -> None:
        """Append a MockStep rule dynamically during test setup."""
        self._scenario.steps.append(step)

    async def probe(self, *, api_key: str | None = None) -> ProviderCapabilities:
        """Return deterministic ProviderCapabilities without network I/O."""
        _ = api_key
        return self._capabilities

    async def stream_chat(
        self,
        req: ChatRequest,
        *,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Yield reasoning, content, tool calls, usage, and finish deltas or configured fault."""
        key_id = compute_key_fingerprint(api_key) if api_key else None
        self.calls.append(RecordedCall(request=req, key_id=key_id))
        step = self._match_step(req, key_id=key_id)

        prompt_text = "\n".join(m.content for m in req.messages)
        prompt_tokens = self._token_counter.count(prompt_text)

        if step is not None and step.fault is not None:
            async for delta in self._emit_fault(step):
                yield delta
            return

        r_chunks = (
            step.reasoning_chunks if step is not None else (self._scenario.default_reasoning,)
        )
        c_chunks = step.content_chunks if step is not None else (self._scenario.default_content,)
        t_calls = step.tool_calls if step is not None else ()
        finish_reason = step.finish_reason if step is not None else "stop"

        reasoning_full = "".join(r_chunks)
        content_full = "".join(c_chunks)

        for r_chunk in r_chunks:
            if r_chunk:
                yield ReasoningDelta(text=r_chunk)

        for c_chunk in c_chunks:
            if c_chunk:
                yield ContentDelta(text=c_chunk)

        for idx, tc in enumerate(t_calls):
            yield ToolCallDelta(index=idx, id=tc.id, name=tc.name, arguments_chunk=tc.arguments)

        r_tokens = self._token_counter.count(reasoning_full)
        c_tokens = self._token_counter.count(content_full)
        yield UsageDelta(
            prompt_tokens=prompt_tokens,
            completion_tokens=c_tokens,
            reasoning_tokens=r_tokens,
            total_tokens=prompt_tokens + c_tokens + r_tokens,
        )

        if finish_reason == "length":
            raise TruncatedOutput(
                "MockProvider simulated finish_reason=length",
                partial_content=content_full,
            )
        if finish_reason == "content_filter":
            raise ContentFiltered("MockProvider simulated content_filter")
        yield FinishDelta(reason=finish_reason)

    async def _emit_fault(self, step: MockStep) -> AsyncIterator[StreamDelta]:
        fault = step.fault
        if fault == "rate_limit":
            raise RateLimited("Mock 429 rate limit", retry_after_s=step.retry_after_s)
        if fault == "auth_failed":
            raise AuthFailed("Mock 401 unauthorized")
        if fault == "bad_request":
            raise BadRequest("Mock 400 bad request")
        if fault == "context_overflow":
            raise ContextOverflow("Mock 400 maximum context length exceeded")
        if fault == "server_error":
            raise ProviderUnavailable("Mock 503 provider unavailable")
        if fault == "timeout":
            raise RequestTimeout("Mock request timed out")
        if fault == "content_filter":
            raise ContentFiltered("Mock content filter triggered")
        if fault == "stream_interrupted":
            partial_r = "".join(step.reasoning_chunks[:1])
            partial_c = "".join(step.content_chunks[:1])
            if partial_r:
                yield ReasoningDelta(text=partial_r)
            if partial_c:
                yield ContentDelta(text=partial_c)
            raise StreamInterrupted(
                "Mock stream interrupted mid-flight",
                partial_content=partial_c,
                partial_reasoning=partial_r,
            )
        if fault == "truncated_length":
            partial_c = "".join(step.content_chunks)
            for chunk in step.content_chunks:
                if chunk:
                    yield ContentDelta(text=chunk)
            raise TruncatedOutput("Mock truncated length", partial_content=partial_c)

    def _match_step(self, req: ChatRequest, *, key_id: str | None) -> MockStep | None:
        combined_prompt = "\n".join(m.content for m in req.messages)
        for idx, step in enumerate(self._scenario.steps):
            used = self._step_uses.get(idx, 0)
            if step.max_uses is not None and used >= step.max_uses:
                continue
            if step.role is not None and step.role != req.role:
                continue
            if step.node_id is not None and step.node_id != req.node_id:
                continue
            if step.key_id is not None and step.key_id != key_id:
                continue
            if step.prompt_regex is not None and not re.search(step.prompt_regex, combined_prompt):
                continue
            self._step_uses[idx] = used + 1
            return step
        return None

    async def count_tokens(self, text: str) -> int:
        """Count tokens deterministically."""
        return await self._token_counter.count_async(text)

    async def close(self) -> None:
        """Mark provider closed."""
        self.closed = True
