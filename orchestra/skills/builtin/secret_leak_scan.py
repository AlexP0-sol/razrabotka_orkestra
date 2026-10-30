"""Built-in skill: detect leaked API keys, tokens, and private keys via regex + entropy."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "secret_leak_scan",
    "name": "Secret & Credential Leak Scanner",
    "version": "1.0.0",
    "description": "Scan text or directory for API keys (nvapi-, sk-, AKIA, PEM, Telegram tokens).",
    "tags": ("security", "secrets", "audit"),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "target_path": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["clean", "findings"],
        "properties": {
            "clean": {"type": "boolean"},
            "findings": {"type": "array"},
        },
    },
}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NVIDIA_API_KEY", re.compile(r"nvapi-(?!xxxx|yyyy|REDACTED)[A-Za-z0-9_-]{16,}")),
    ("OPENAI_KEY", re.compile(r"sk-(?!proj-xxxx|xxxx|REDACTED)[A-Za-z0-9_-]{20,}")),
    ("AWS_ACCESS_KEY", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("PRIVATE_KEY_PEM", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
)


def _shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(token)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def scan_text(text: str, *, source_name: str = "<memory>") -> list[dict[str, Any]]:
    """Scan `text` line-by-line returning redacted credential findings."""
    findings: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule_name, pat in SECRET_PATTERNS:
            match = pat.search(line)
            if match is not None:
                matched = match.group(0)
                if rule_name != "PRIVATE_KEY_PEM" and _shannon_entropy(matched) < 3.0:
                    continue
                findings.append(
                    {
                        "source": source_name,
                        "line": lineno,
                        "rule": rule_name,
                        "preview": f"{matched[:6]}...REDACTED",
                    }
                )
    return findings


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Scan inline `text` or files under `target_path` for secret leaks."""
    findings: list[dict[str, Any]] = []
    if payload.get("text"):
        findings.extend(scan_text(str(payload["text"]), source_name="inline"))
    if payload.get("target_path"):
        root = Path(str(payload["target_path"]))
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for fpath in files:
            if any(part.startswith(".git") or part == ".venv" for part in fpath.parts):
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            findings.extend(scan_text(content, source_name=str(fpath)))
    return {"clean": len(findings) == 0, "findings": findings}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
