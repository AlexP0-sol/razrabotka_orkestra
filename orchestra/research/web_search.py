"""Lexical BM25-style offline documentation search with optional allowlisted HTTP fetch (RS-04)."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from orchestra.errors import SecurityViolation
from orchestra.research.doc_reader import DocSnippet, OfflineDocReader, sanitize_external_document

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9_]{2,}")


class SearchHit(BaseModel):
    """Ranked documentation snippet returned by `ResearchSearchEngine`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    title: str
    source: str
    score: float
    excerpt: str


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(text)]


class ResearchSearchEngine:
    """Searches cached offline documentation snippets and optionally fetches allowlisted URLs."""

    def __init__(
        self,
        doc_reader: OfflineDocReader,
        *,
        allowed_domains: set[str] | None = None,
    ) -> None:
        """Initialize ResearchSearchEngine."""
        self._reader = doc_reader
        self._allowed_domains = {d.lower() for d in (allowed_domains or set())}

    def search_offline(self, query: str, *, top_k: int = 5) -> list[SearchHit]:
        """Rank cached `DocSnippet`s against `query` using IDF-weighted lexical scoring."""
        q_terms = _tokenize(query)
        snippets = self._reader.list_cached()
        if not q_terms or not snippets:
            return []

        doc_tokens: list[tuple[DocSnippet, list[str]]] = [
            (s, _tokenize(f"{s.title} {s.content}")) for s in snippets
        ]
        n_docs = len(doc_tokens)
        df: dict[str, int] = {}
        for _, toks in doc_tokens:
            unique = set(toks)
            for term in set(q_terms):
                if term in unique:
                    df[term] = df.get(term, 0) + 1

        hits: list[SearchHit] = []
        for snippet, toks in doc_tokens:
            if not toks:
                continue
            tf_map: dict[str, int] = {}
            for tok in toks:
                tf_map[tok] = tf_map.get(tok, 0) + 1
            score = 0.0
            for term in q_terms:
                tf = tf_map.get(term, 0)
                if tf > 0:
                    idf = math.log(1.0 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
                    score += (1.0 + math.log(tf)) * idf
            if score > 0:
                hits.append(
                    SearchHit(
                        doc_id=snippet.doc_id,
                        title=snippet.title,
                        source=snippet.source_path,
                        score=round(score, 4),
                        excerpt=snippet.content[:300],
                    )
                )

        hits.sort(key=lambda h: (h.score, h.doc_id), reverse=True)
        return hits[: max(1, top_k)]

    async def fetch_allowlisted_url(self, url: str, *, cache_dir: Path) -> DocSnippet | None:
        """Fetch an allowlisted documentation URL, sanitize it, and store in offline cache."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not self._allowed_domains or host not in self._allowed_domains:
            raise SecurityViolation(
                f"Outbound research fetch to '{host}' blocked: not in allowed_domains"
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw_text = resp.text
        except Exception:
            return None

        clean_txt, _ = sanitize_external_document(raw_text[:12_000])
        tmp_doc = cache_dir / f"fetched_{host.replace('.', '_')}.txt"
        tmp_doc.write_text(clean_txt, encoding="utf-8", newline="\n")
        return self._reader.ingest_file(tmp_doc)
