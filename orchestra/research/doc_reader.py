"""Offline documentation reader with HTML stripping, caching, and injection sanitization (RS-03)."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from orchestra.state.event_log import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
        r"disregard\s+(?:all\s+)?(?:previous|system)\s+prompts?",
        r"<\s*/?\s*system\s*>",
        r"\bSYSTEM\s*:\s*you\s+are\b",
        r"exfiltrate\s+(?:api|secret|env)",
    )
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag.lower() in ("p", "div", "li", "tr", "h1", "h2", "h3", "br"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def sanitize_external_document(raw_text: str) -> tuple[str, bool]:
    """Strip prompt-injection directives from untrusted docs and report if modified (SEC-03)."""
    cleaned = raw_text
    modified = False
    for pat in INJECTION_PATTERNS:
        if pat.search(cleaned):
            cleaned = pat.sub("[REDACTED_PROMPT_INJECTION]", cleaned)
            modified = True
    return cleaned, modified


class DocSnippet(BaseModel):
    """Sanitized and cached documentation excerpt wrapped for safe prompt grounding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    source_path: str
    title: str
    content: str
    sha256: str
    injection_redacted: bool = False

    def render_isolated_block(self) -> str:
        """Wrap untrusted content inside explicit XML boundary tags (SEC-03)."""
        return (
            f'<untrusted_external_doc id="{self.doc_id}" source="{self.source_path}">\n'
            f"{self.content}\n"
            "</untrusted_external_doc>"
        )


class OfflineDocReader:
    """Reads Markdown, plain text, RST, and HTML docs into `runtime/docs_cache/`."""

    def __init__(self, docs_cache_dir: Path, *, max_chars_per_doc: int = 12_000) -> None:
        """Initialize OfflineDocReader."""
        self._cache_dir = docs_cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_chars = max_chars_per_doc

    def ingest_file(self, path: Path) -> DocSnippet:
        """Read, strip HTML if needed, sanitize against prompt injection, and cache snippet."""
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in (".html", ".htm"):
            parser = _TextExtractor()
            parser.feed(raw)
            extracted = parser.get_text()
        else:
            extracted = raw

        clipped = extracted[: self._max_chars]
        sanitized, was_modified = sanitize_external_document(clipped)
        digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        doc_id = f"doc_{digest[:10]}"
        snippet = DocSnippet(
            doc_id=doc_id,
            source_path=str(path),
            title=path.stem,
            content=sanitized,
            sha256=digest,
            injection_redacted=was_modified,
        )
        atomic_write_text(
            self._cache_dir / f"{doc_id}.json",
            snippet.model_dump_json(indent=2),
        )
        return snippet

    def list_cached(self) -> list[DocSnippet]:
        """Load all cached `DocSnippet` records from `docs_cache_dir`."""
        items: list[DocSnippet] = []
        for fpath in sorted(self._cache_dir.glob("doc_*.json")):
            try:
                items.append(DocSnippet.model_validate_json(fpath.read_text(encoding="utf-8")))
            except Exception:
                continue
        return items
