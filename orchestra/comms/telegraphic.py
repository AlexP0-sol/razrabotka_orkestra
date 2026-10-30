"""ASCII Telegraphic English validator and compressor for inter-agent RPC (§14.1, LP-01..06)."""

from __future__ import annotations

import re

from orchestra.config.constants import (
    RPC_DEFECT_TOKEN_LIMIT,
    RPC_NOTE_TOKEN_LIMIT,
    RPC_SUMMARY_TOKEN_LIMIT,
)
from orchestra.errors import RpcStyleViolation
from orchestra.providers.token_counter import count_tokens_sync

FORBIDDEN_POLITE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bplease\b",
        r"\bthank\s+you\b",
        r"\bthanks\b",
        r"\bkindly\b",
        r"\bcould\s+you\b",
        r"\bwould\s+you\b",
        r"\bi\s+think\b",
        r"\bi\s+believe\b",
        r"\bhope\s+this\s+helps\b",
        r"\bsorry\b",
        r"\bgreetings\b",
        r"\bdear\s+\w+\b",
        r"\bhello\b",
        r"\bbest\s+regards\b",
    )
)


def validate_telegraphic_field(
    text: str,
    *,
    field_name: str,
    max_tokens: int,
) -> None:
    """Validate that `text` is pure ASCII, free of polite prose, and within `max_tokens`.

    Raises:
        RpcStyleViolation: When any telegraphic protocol invariant is violated.
    """
    if not text:
        return
    if not text.isascii():
        raise RpcStyleViolation(
            f"[NON_ASCII] RPC field '{field_name}' must contain pure 7-bit ASCII only (LP-01)"
        )
    for pattern in FORBIDDEN_POLITE_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise RpcStyleViolation(
                f"[POLITE_FILLER] Forbidden conversational phrase '{match.group(0)}' in "
                f"RPC field '{field_name}' (LP-06, BAN-08)"
            )
    tokens = count_tokens_sync(text)
    if tokens > max_tokens:
        raise RpcStyleViolation(
            f"[TOKEN_LIMIT_EXCEEDED] RPC field '{field_name}' uses {tokens} tokens > "
            f"limit {max_tokens}"
        )


def compress_telegraphic(text: str, *, max_tokens: int = RPC_NOTE_TOKEN_LIMIT) -> str:
    """Deterministically strip non-ASCII/polite filler and truncate to `max_tokens`."""
    ascii_text = text.encode("ascii", errors="ignore").decode("ascii")
    cleaned = ascii_text
    for pattern in FORBIDDEN_POLITE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")
    words = cleaned.split(" ")
    while words and count_tokens_sync(" ".join(words)) > max_tokens:
        words.pop()
    return " ".join(words)


class TelegraphicValidator:
    """Stateless validator enforcing §14.1 token caps across RPC notes, summaries, and defects."""

    def validate_note(self, note: str) -> None:
        """Validate short RPC `note` (`<= 40` tokens)."""
        validate_telegraphic_field(note, field_name="note", max_tokens=RPC_NOTE_TOKEN_LIMIT)

    def validate_summary(self, summary: str) -> None:
        """Validate task/verdict `summary` (`<= 60` tokens)."""
        validate_telegraphic_field(
            summary,
            field_name="summary",
            max_tokens=RPC_SUMMARY_TOKEN_LIMIT,
        )

    def validate_defects(self, defects: tuple[str, ...] | list[str]) -> None:
        """Validate each defect description (`<= 30` tokens each)."""
        for idx, item in enumerate(defects):
            validate_telegraphic_field(
                item,
                field_name=f"defects[{idx}]",
                max_tokens=RPC_DEFECT_TOKEN_LIMIT,
            )
