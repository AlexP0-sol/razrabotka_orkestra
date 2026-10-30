"""Dual-stream router enforcing strict Reasoning vs Content isolation (§13.4, §15, INV-07)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from orchestra.config import constants as c
from orchestra.errors import StreamInterrupted
from orchestra.providers.base import (
    ContentDelta,
    FinishDelta,
    ReasoningDelta,
    StreamDelta,
    ToolCallDelta,
    UsageDelta,
)
from orchestra.providers.token_counter import TokenCounter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from orchestra.config.settings import ReasoningSettings

REASONING_LOOP_WINDOW_CHARS: int = 500
REASONING_LOOP_MIN_LEN: int = 80
REASONING_LOOP_MIN_REPEATS: int = 4
THINKING_UI_THROTTLE_S: float = 0.10
THINKING_UI_MIN_CHARS: int = 200


class ReasoningLoopDetectedError(StreamInterrupted):
    """Raised when repetitive degenerate loops are detected inside reasoning stream (LLM-08)."""


class AssembledToolCall(BaseModel):
    """Completed tool call assembled from streaming ToolCallDelta chunks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    arguments: str


class ThinkingAuditResult(BaseModel):
    """Audit result of the 5-step internal reasoning protocol (§15.4, TH-05)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    matched_markers: tuple[str, ...]
    warning: str | None = None


class RoutedStreamOutput(BaseModel):
    """Strictly segregated output produced by `DualStreamRouter` (INV-07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_text: str
    reasoning_text: str
    tool_calls: tuple[AssembledToolCall, ...]
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int
    usage_estimated: bool
    finish_reason: str


@dataclass
class _ToolAccumulator:
    id: str = ""
    name: str = ""
    args_parts: list[str] = field(default_factory=list)


def resolve_reasoning_budget(
    role: str,
    reasoning_settings: ReasoningSettings | None = None,
    *,
    trivial: bool = False,
) -> int:
    """Resolve role-specific token budget for internal thinking (§15.3, TH-02)."""
    if trivial:
        return reasoning_settings.trivial_budget if reasoning_settings else c.REASONING_TRIVIAL
    role_lower = role.lower()
    if any(k in role_lower for k in ("architect", "judge", "security", "qa", "test", "adversar")):
        return reasoning_settings.architect_budget if reasoning_settings else c.REASONING_ARCHITECT
    if any(k in role_lower for k in ("ui", "frontend", "web")):
        return reasoning_settings.ui_budget if reasoning_settings else c.REASONING_UI
    if any(k in role_lower for k in ("format", "summary", "rpc", "trivial")):
        return reasoning_settings.trivial_budget if reasoning_settings else c.REASONING_TRIVIAL
    return reasoning_settings.developer_budget if reasoning_settings else c.REASONING_DEV


def detect_reasoning_loop(
    tail_text: str,
    *,
    min_fragment_len: int = REASONING_LOOP_MIN_LEN,
    min_repeats: int = REASONING_LOOP_MIN_REPEATS,
) -> bool:
    """Detect >= `min_repeats` consecutive repeats of a substring >= `min_fragment_len`."""
    window = tail_text[-REASONING_LOOP_WINDOW_CHARS * 2 :]
    n = len(window)
    max_frag = n // min_repeats
    if max_frag < min_fragment_len:
        return False
    for length in range(min_fragment_len, max_frag + 1):
        suffix = window[n - length :]
        if suffix * min_repeats == window[n - length * min_repeats :]:
            return True
    return False


def audit_thinking_protocol(reasoning_text: str) -> ThinkingAuditResult:
    """Audit whether reasoning followed the 5-step self-verification protocol (§15.4, TH-05)."""
    upper = reasoning_text.upper()
    checks = (
        ("BOUNDARY", ("BOUNDARY", "1.", "1 ", "INPUT", "EDGE")),
        ("DIARY", ("DIARY", "ANTIPATTERN", "2.", "2 ")),
        ("VULNERABILITY", ("VULN", "RACE", "SECURITY", "LEAK", "3.", "3 ")),
        ("SKELETON", ("SKELETON", "CONTRACT", "SCHEMA", "4.", "4 ")),
        ("CRITIQUE", ("CRITIQUE", "SELF", "OPTIMAL", "VERIFY", "5.", "5 ")),
    )
    matched: list[str] = []
    for label, tokens in checks:
        if any(tok in upper for tok in tokens):
            matched.append(label)
    passed = len(matched) >= 3
    warning = (
        None
        if passed
        else f"Reasoning protocol incomplete: matched {len(matched)}/5 steps ({matched})"
    )
    return ThinkingAuditResult(passed=passed, matched_markers=tuple(matched), warning=warning)


class DualStreamRouter:
    """Consumes `StreamDelta` events and guarantees `reasoning` never enters `content` (INV-07)."""

    def __init__(
        self,
        *,
        token_counter: TokenCounter | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        thinking_log_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize DualStreamRouter."""
        self._token_counter = token_counter or TokenCounter()
        self._on_thinking_delta = on_thinking_delta
        self._on_content_delta = on_content_delta
        self._thinking_log_dir = thinking_log_dir
        self._clock = clock

    async def consume(
        self,
        stream: AsyncIterator[StreamDelta],
        *,
        request_id: str,
        prompt_text: str = "",
    ) -> RoutedStreamOutput:
        """Consume stream deltas into strictly separated buffers with loop and token audit."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        pending_ui_thinking: list[str] = []
        last_ui_flush = self._clock()
        tool_map: dict[int, _ToolAccumulator] = {}
        usage_seen: UsageDelta | None = None
        finish_reason = "stop"

        async for delta in stream:
            if isinstance(delta, ReasoningDelta):
                reasoning_parts.append(delta.text)
                pending_ui_thinking.append(delta.text)
                now = self._clock()
                pending_len = sum(len(x) for x in pending_ui_thinking)
                if self._on_thinking_delta is not None and (
                    (now - last_ui_flush) >= THINKING_UI_THROTTLE_S
                    or pending_len >= THINKING_UI_MIN_CHARS
                ):
                    self._on_thinking_delta("".join(pending_ui_thinking))
                    pending_ui_thinking.clear()
                    last_ui_flush = now

                tail = "".join(reasoning_parts[-16:])
                if detect_reasoning_loop(tail):
                    raise ReasoningLoopDetectedError(
                        "Degenerate repetition loop detected in reasoning stream",
                        partial_content="".join(content_parts),
                        partial_reasoning="".join(reasoning_parts),
                    )
            elif isinstance(delta, ContentDelta):
                content_parts.append(delta.text)
                if self._on_content_delta is not None:
                    self._on_content_delta(delta.text)
            elif isinstance(delta, ToolCallDelta):
                acc = tool_map.setdefault(delta.index, _ToolAccumulator())
                if delta.id:
                    acc.id = delta.id
                if delta.name:
                    acc.name = delta.name
                if delta.arguments_chunk:
                    acc.args_parts.append(delta.arguments_chunk)
            elif isinstance(delta, UsageDelta):
                usage_seen = delta
            elif isinstance(delta, FinishDelta):
                finish_reason = delta.reason

        if pending_ui_thinking and self._on_thinking_delta is not None:
            self._on_thinking_delta("".join(pending_ui_thinking))

        content_full = "".join(content_parts)
        reasoning_full = "".join(reasoning_parts)

        if self._thinking_log_dir is not None and reasoning_full:
            self._thinking_log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self._thinking_log_dir / f"{request_id}.log"
            log_file.write_text(reasoning_full, encoding="utf-8", newline="\n")

        assembled_tools = tuple(
            AssembledToolCall(
                id=acc.id or f"call_{idx}",
                name=acc.name,
                arguments="".join(acc.args_parts),
            )
            for idx, acc in sorted(tool_map.items())
            if acc.name
        )

        if usage_seen is not None and usage_seen.total_tokens > 0:
            p_tok = usage_seen.prompt_tokens
            c_tok = usage_seen.completion_tokens
            r_tok = usage_seen.reasoning_tokens or self._token_counter.count(reasoning_full)
            tot_tok = max(usage_seen.total_tokens, p_tok + c_tok + r_tok)
            estimated = False
        else:
            p_tok = self._token_counter.count(prompt_text)
            c_tok = self._token_counter.count(content_full)
            r_tok = self._token_counter.count(reasoning_full)
            tot_tok = p_tok + c_tok + r_tok
            estimated = True

        return RoutedStreamOutput(
            content_text=content_full,
            reasoning_text=reasoning_full,
            tool_calls=assembled_tools,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            reasoning_tokens=r_tok,
            total_tokens=tot_tok,
            usage_estimated=estimated,
            finish_reason=finish_reason,
        )
