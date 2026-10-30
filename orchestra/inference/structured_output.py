"""Structured JSON output extraction, Pydantic v2 validation, and repair prompt builder (LLM-07)."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from orchestra.errors import StructuredOutputError
from orchestra.providers.base import ChatMessage

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_candidates(content_text: str) -> list[str]:
    """Extract candidate JSON substrings strictly from `content_text` (never reasoning)."""
    stripped = content_text.strip()
    if not stripped:
        return []

    candidates: list[str] = [stripped]
    for match in _FENCED_JSON_RE.finditer(stripped):
        fenced = match.group(1).strip()
        if fenced and fenced not in candidates:
            candidates.append(fenced)

    balanced = _extract_first_balanced_json(stripped)
    if balanced and balanced not in candidates:
        candidates.append(balanced)

    return candidates


def _extract_first_balanced_json(text: str) -> str | None:
    start_idx = -1
    open_ch = ""
    close_ch = ""
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            start_idx = i
            open_ch = ch
            close_ch = "}" if ch == "{" else "]"
            break
    if start_idx < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start_idx : idx + 1]
    return None


def parse_structured_content(content_text: str, schema: type[ModelT]) -> ModelT:
    """Parse and validate `content_text` against Pydantic `schema` (LLM-07 steps 1-2).

    Raises:
        StructuredOutputError: When no valid JSON matching `schema` can be extracted.
    """
    candidates = extract_json_candidates(content_text)
    if not candidates:
        raise StructuredOutputError("Empty content response; expected JSON object", raw_output="")

    last_error = "No JSON object found"
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"JSONDecodeError: {exc}"
            continue
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            last_error = f"ValidationError: {exc}"

    raise StructuredOutputError(
        f"Failed to validate structured output for {schema.__name__}: {last_error}",
        raw_output=content_text,
    )


def build_json_repair_messages(
    *,
    broken_content: str,
    validation_error: str,
    schema: type[BaseModel],
) -> tuple[ChatMessage, ChatMessage]:
    """Build minimal fast-repair prompt (`enable_thinking=False`, `max_output_tokens=2048`)."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=True)
    sys_msg = ChatMessage(
        role="system",
        content=(
            "You are a deterministic JSON repair utility. Return ONLY valid raw JSON matching "
            "the provided JSON Schema. Do not wrap in markdown or add prose."
        ),
    )
    user_msg = ChatMessage(
        role="user",
        content=(
            f"JSON_SCHEMA:\n{schema_json}\n\n"
            f"VALIDATION_ERROR:\n{validation_error}\n\n"
            f"BROKEN_PAYLOAD:\n{broken_content[:8000]}"
        ),
    )
    return (sys_msg, user_msg)
