"""Builtin skill §13.9.3: offline BM25 search over local documentation corpus."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from orchestra.research.doc_reader import OfflineDocReader
from orchestra.research.web_search import ResearchSearchEngine

SKILL_METADATA: dict[str, Any] = {
    "id": "search_documentation",
    "name": "Search Documentation",
    "version": "1.0.0",
    "description": "Search local documentation snippets via deterministic BM25 index.",
    "tags": ("research", "docs", "bm25"),
    "input_schema": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "stack": {"type": "string"},
            "max_results": {"type": "integer"},
            "corpus": {"type": "object"},
        },
    },
    "output_schema": {"type": "object", "required": ["snippets"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Search provided or default documentation corpus and return ranked snippets."""
    query = str(payload["query"])
    stack = str(payload.get("stack", "python"))
    max_results = int(payload.get("max_results", 5))
    raw_corpus = payload.get("corpus")
    docs: dict[str, str] = (
        {str(k): str(v) for k, v in raw_corpus.items()}
        if isinstance(raw_corpus, dict) and raw_corpus
        else {
            f"{stack}_decimal.md": (
                "Use decimal.Decimal from strings for all monetary quantities and prices."
            ),
            f"{stack}_asyncio.md": (
                "Use asyncio.TaskGroup and asyncio.timeout on Python 3.11+ runtimes."
            ),
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        reader = OfflineDocReader(cache_dir)
        for fname, content in docs.items():
            fpath = cache_dir / fname
            fpath.write_text(content, encoding="utf-8")
            reader.ingest_file(fpath)
        engine = ResearchSearchEngine(reader)
        hits = engine.search_offline(query, top_k=max_results)
    return {
        "snippets": [
            {"doc_id": h.doc_id, "score": round(h.score, 4), "snippet": h.excerpt} for h in hits
        ]
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps({"ok": True, "result": run(data)}))
