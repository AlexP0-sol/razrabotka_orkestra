"""Universal secret redactor for logs, traces, errors, and state snapshots (INV-07, SEC-01..02)."""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

REDACTED_MASK = "***REDACTED***"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|secret|password|passphrase|access[_-]?token)\s*[:=]\s*['\"]?)"
        r"([^\s,'\"&]{6,})"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
)

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "bot_token",
        "access_token",
        "secret",
        "api_secret",
        "password",
        "passphrase",
        "private_key",
        "authorization",
    }
)


class SecretRedactor:
    """Scrubs static regex secret patterns and dynamically registered runtime credentials."""

    def __init__(self, initial_secrets: list[str] | tuple[str, ...] = ()) -> None:
        """Initialize SecretRedactor with optional exact secret strings."""
        self._exact_secrets: set[str] = set()
        for item in initial_secrets:
            self.register_secret(item)

    def register_secret(self, secret: str) -> None:
        """Register an exact secret value (length >= 6) for guaranteed redaction."""
        cleaned = secret.strip()
        if len(cleaned) >= 6:
            self._exact_secrets.add(cleaned)

    def redact_text(self, text: str) -> str:
        """Scrub all registered secrets and regex credential patterns from `text`."""
        if not text:
            return text
        out = text
        for exact in sorted(self._exact_secrets, key=len, reverse=True):
            if exact in out:
                out = out.replace(exact, REDACTED_MASK)

        for idx, pattern in enumerate(_SECRET_PATTERNS):
            if idx in (4, 5):
                out = pattern.sub(rf"\1{REDACTED_MASK}", out)
            else:
                out = pattern.sub(REDACTED_MASK, out)
        return out

    def redact_value(self, value: Any, *, field_name: str | None = None) -> Any:
        """Recursively scrub strings, dicts, lists, and tuples."""
        if field_name and field_name.lower() in _SENSITIVE_FIELD_NAMES and value is not None:
            return REDACTED_MASK
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {str(k): self.redact_value(v, field_name=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in value)
        return value

    def structlog_processor(
        self,
        _logger: Any,
        _method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> Mapping[str, Any]:
        """`structlog` processor scrubbing every field of `event_dict` before emission."""
        redacted = self.redact_value(dict(event_dict))
        return redacted if isinstance(redacted, dict) else event_dict


DEFAULT_REDACTOR = SecretRedactor()
