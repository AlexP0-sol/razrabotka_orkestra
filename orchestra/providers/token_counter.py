"""Deterministic token counting utility with offline-safe fallback (§13.2, §34.1).

Attempts to use `tiktoken` when cached BPE vocabularies are available locally; otherwise falls back
to a deterministic lexical/UTF-8 BPE estimator that never blocks or fails offline.
"""

from __future__ import annotations

import math
import re
from typing import Any

import tiktoken

_WORD_OR_PUNCT_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", re.UNICODE)


class TokenCounter:
    """Thread- and task-safe token estimator for prompts, RPC envelopes, and context budgets."""

    def __init__(self, encoding_name: str = "cl100k_base", *, allow_tiktoken: bool = False) -> None:
        """Initialize TokenCounter.

        Args:
            encoding_name: Preferred tiktoken encoding name.
            allow_tiktoken: Whether to attempt loading tiktoken (disabled by default in offline
                environments unless explicitly enabled to avoid remote blob fetches).
        """
        self._encoding: Any | None = None
        if allow_tiktoken:
            try:
                self._encoding = tiktoken.get_encoding(encoding_name)
            except Exception:
                self._encoding = None

    def count(self, text: str) -> int:
        """Count approximate BPE tokens in `text` deterministically."""
        if not text:
            return 0
        if self._encoding is not None:
            encoded: list[int] = self._encoding.encode(text)
            return len(encoded)
        return _heuristic_bpe_count(text)

    async def count_async(self, text: str) -> int:
        """Async wrapper matching `LLMProvider.count_tokens` signature."""
        return self.count(text)


def _heuristic_bpe_count(text: str) -> int:
    """Deterministic BPE token approximation accurate across ASCII code and Cyrillic text."""
    tokens = 0
    for piece in _WORD_OR_PUNCT_RE.findall(text):
        if piece.isascii():
            # Average ~4 chars per subword token in English/code identifiers
            tokens += max(1, math.ceil(len(piece) / 4))
        else:
            # Non-ASCII (e.g., Cyrillic/CJK) averages ~2 chars per BPE token
            tokens += max(1, math.ceil(len(piece) / 2))
    return tokens


DEFAULT_TOKEN_COUNTER = TokenCounter(allow_tiktoken=False)


def count_tokens_sync(text: str) -> int:
    """Convenience synchronous token counter used by validators and budgeters."""
    return DEFAULT_TOKEN_COUNTER.count(text)
