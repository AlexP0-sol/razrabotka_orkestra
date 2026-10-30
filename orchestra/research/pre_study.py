"""Stage S1 Pre-Study runner detecting domain packs, grounding packages, and reading local docs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from orchestra.research.doc_reader import DocSnippet, OfflineDocReader
from orchestra.research.grounding import ApiSymbolFact, GroundingInspector

if TYPE_CHECKING:
    from pathlib import Path

CRYPTO_KEYWORDS_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"crypto|binance|bybit|okx|kraken|bitget|gateio|kucoin|hyperliquid|"
    r"ohlcv|orderbook|perpetual|perp|futures|funding\s+rate|kill\s*switch|"
    r"backtest|sharpe|drawdown|slippage|крипт\w*|бирж\w*|фьючерс\w*|ордер\w*|свеч\w*|бэктест\w*"
    r")\b",
    re.IGNORECASE,
)

CORE_PACKAGES_TO_PROBE: tuple[str, ...] = (
    "pydantic",
    "pydantic-settings",
    "fastapi",
    "httpx",
    "openai",
    "aiogram",
    "pytest",
    "ruff",
    "mypy",
)


def detect_project_domain(specification_text: str) -> str:
    """Auto-detect `'crypto_trading'` vs `'general'` domain from customer spec (§38.1, DP-01)."""
    matches = CRYPTO_KEYWORDS_RE.findall(specification_text)
    if len(matches) >= 2:
        return "crypto_trading"
    return "general"


class PreStudyBrief(BaseModel):
    """Verified S1 Pre-Study dossier passed to Lead Architect before S2 design (RS-01..02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    detected_domain: str
    installed_packages: dict[str, str]
    grounded_symbols: tuple[ApiSymbolFact, ...]
    doc_snippets: tuple[DocSnippet, ...]
    warnings: tuple[str, ...]
    summary_ru: str


class PreStudyRunner:
    """Executes S1 Pre-Study grounding runtime versions, APIs, and local documentation."""

    def __init__(
        self,
        *,
        docs_cache_dir: Path,
        inspector: GroundingInspector | None = None,
    ) -> None:
        """Initialize PreStudyRunner."""
        self._reader = OfflineDocReader(docs_cache_dir)
        self._inspector = inspector or GroundingInspector()

    def run(
        self,
        *,
        project_id: str,
        specification_text: str,
        symbols_to_verify: tuple[str, ...] = ("pydantic.BaseModel", "httpx.AsyncClient"),
        local_doc_paths: tuple[Path, ...] = (),
    ) -> PreStudyBrief:
        """Produce a grounded `PreStudyBrief` without hallucinating missing libraries."""
        domain = detect_project_domain(specification_text)

        installed: dict[str, str] = {}
        warnings: list[str] = []
        for dist in CORE_PACKAGES_TO_PROBE:
            ver = self._inspector.get_package_version(dist)
            if ver is not None:
                installed[dist] = ver
            else:
                warnings.append(f"Package '{dist}' is not installed in runtime")

        facts: list[ApiSymbolFact] = []
        for sym in symbols_to_verify:
            try:
                facts.append(self._inspector.inspect_symbol(sym))
            except Exception as exc:
                warnings.append(str(exc))

        snippets: list[DocSnippet] = []
        for doc_path in local_doc_paths:
            if doc_path.is_file():
                snippets.append(self._reader.ingest_file(doc_path))

        summary_ru = (
            f"Предпроектное исследование (S1) завершено: домен='{domain}', "
            f"проверено пакетов={len(installed)}, подтверждено API-символов={len(facts)}, "
            f"загружено документов={len(snippets)}."
        )
        return PreStudyBrief(
            project_id=project_id,
            detected_domain=domain,
            installed_packages=installed,
            grounded_symbols=tuple(facts),
            doc_snippets=tuple(snippets),
            warnings=tuple(warnings),
            summary_ru=summary_ru,
        )
