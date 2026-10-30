"""Core LLM provider protocols, request DTOs, and stream deltas (§13.2.1, PRV-01..04)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def compute_key_fingerprint(raw_key: str) -> str:
    """Return stable 8-hex-character SHA-256 fingerprint of an API key (OAI-08, BAN-04)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:8]


class ChatMessage(BaseModel):
    """Normalized chat message passed to LLM providers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolDefinition(BaseModel):
    """JSON-Schema function tool declaration exposed to tool-capable models."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    """Immutable LLM streaming invocation request (PRV-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    model: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    temperature: float = 0.0
    top_p: float | None = None
    seed: int | None = 1337
    max_output_tokens: int = 16_384
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 180.0
    role: str = "developer"
    node_id: str | None = None
    agent_id: str = "system"


class ReasoningDelta(BaseModel):
    """Reasoning/thinking stream delta routed strictly to audit & UI (INV-07, PRV-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["reasoning"] = "reasoning"
    text: str


class ContentDelta(BaseModel):
    """Final content stream delta routed to artifact parsers (INV-07, PRV-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["content"] = "content"
    text: str


class ToolCallDelta(BaseModel):
    """Incremental function/tool call delta (PRV-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tool_call"] = "tool_call"
    index: int
    id: str | None = None
    name: str | None = None
    arguments_chunk: str = ""


class UsageDelta(BaseModel):
    """Token usage summary emitted at the end of a stream (PRV-02, INV-10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class FinishDelta(BaseModel):
    """Terminal stream marker carrying provider finish_reason (PRV-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["finish"] = "finish"
    reason: str


StreamDelta = Annotated[
    ReasoningDelta | ContentDelta | ToolCallDelta | UsageDelta | FinishDelta,
    Field(discriminator="kind"),
]


class ProviderCapabilities(BaseModel):
    """Probed or safely degraded capabilities of an LLM endpoint (PRV-03, PRV-04)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    exists: bool = True
    max_context_tokens: int = 131_072
    supports_reasoning_stream: bool = True
    reasoning_field_name: str = "reasoning_content"
    supports_extra_body: bool = True
    supports_chat_template_kwargs: bool = True
    supports_reasoning_budget: bool = True
    supports_tools: bool = True
    supports_streaming_tools: bool = True
    min_temperature: float = 0.0
    supports_seed: bool = True
    supports_json_mode: bool = True
    rate_limit_rpm: int = 40
    rate_limit_tpm: int = 120_000
    capabilities_assumed: bool = False
    probed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic interface for streaming chat completion (PRV-01..04)."""

    name: str

    async def probe(self, *, api_key: str | None = None) -> ProviderCapabilities:
        """Probe model availability and supported streaming parameters."""
        ...

    def stream_chat(
        self,
        req: ChatRequest,
        *,
        api_key: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Stream normalized `StreamDelta` events for `req`."""
        ...

    async def count_tokens(self, text: str) -> int:
        """Count tokens for `text`."""
        ...

    async def close(self) -> None:
        """Release underlying HTTP transports and resources."""
        ...
