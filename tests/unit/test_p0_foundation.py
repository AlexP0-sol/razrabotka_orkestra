"""Phase P0 foundation tests verifying repository layout, ADRs, configs, and secret hygiene."""

from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING

import yaml

import orchestra

if TYPE_CHECKING:
    from pathlib import Path

REQUIRED_ROOT_FILES = (
    "ARCHITECTURE.md",
    "README.md",
    "Makefile",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "exchanges.yaml",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "ruff.toml",
    "mypy.ini",
    "pytest.ini",
    "docs/DEFAULTS.md",
    "docs/PROGRESS.md",
    "prompts/MASTER_PROMPT.md",
    "prompts/SOURCE_ANALYSIS.md",
)

REQUIRED_SUBPACKAGES = (
    "config",
    "providers",
    "inference",
    "state",
    "agents",
    "swarm",
    "comms",
    "skills",
    "skills/builtin",
    "skills/builtin/crypto",
    "talents",
    "verify",
    "research",
    "prompts",
    "interfaces",
    "telemetry",
    "domains",
    "security",
)


def test_package_version() -> None:
    """Verify orchestra package exposes semantic version."""
    assert orchestra.__version__ == "0.1.0"


def test_required_root_files_exist(repo_root: Path) -> None:
    """Verify all mandatory P0 files exist at the repository root (§10)."""
    for rel_path in REQUIRED_ROOT_FILES:
        target = repo_root / rel_path
        assert target.is_file(), f"Missing required P0 file: {rel_path}"


def test_required_subpackages_exist(repo_root: Path) -> None:
    """Verify all L0-L7 subpackages are initialized under orchestra/."""
    for subpkg in REQUIRED_SUBPACKAGES:
        init_file = repo_root / "orchestra" / subpkg / "__init__.py"
        assert init_file.is_file(), f"Missing __init__.py for subpackage: {subpkg}"


def test_adr_registry_minimum_count(repo_root: Path) -> None:
    """Verify at least 8 ADRs exist including ADR-0001 and ADR-0002 (DoD-34)."""
    adr_dir = repo_root / "docs" / "adr"
    adrs = sorted(p.name for p in adr_dir.glob("ADR-*.md"))
    assert len(adrs) >= 8
    assert "ADR-0001-own-runtime-vs-langgraph.md" in adrs
    assert "ADR-0002-dependency-baseline.md" in adrs


def test_gitignore_and_gitattributes_rules(repo_root: Path) -> None:
    """Verify runtime/ and .env are ignored and LF line endings are enforced (FP-13, WIN-11)."""
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/" in gitignore
    assert ".env" in gitignore

    gitattributes = (repo_root / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=lf" in gitattributes


def test_pyproject_forbids_external_agent_frameworks(repo_root: Path) -> None:
    """Verify forbidden agent frameworks are absent from dependencies (C-15, BAN-15)."""
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(pyproject["project"]["dependencies"]).lower()
    forbidden = ("langgraph", "langchain", "autogen", "crewai", "llama-index", "openai-agents")
    for pkg in forbidden:
        assert pkg not in deps


def test_exchanges_registry_is_fail_closed(repo_root: Path) -> None:
    """Verify exchanges.yaml parses cleanly and defaults to enabled=[] (DP-78, DP-88)."""
    data = yaml.safe_load((repo_root / "exchanges.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["enabled"] == []
    assert "binance" in data["exchanges"]
    assert "bybit" in data["exchanges"]


def test_no_real_secrets_in_env_example_or_html(repo_root: Path) -> None:
    """Verify neither .env.example nor razrabotka_orkestra.html contains leaked API keys."""
    secret_pattern = re.compile(r"nvapi-(?!xxxx|yyyy|REDACTED)[A-Za-z0-9_-]{12,}")
    env_text = (repo_root / ".env.example").read_text(encoding="utf-8")
    html_text = (repo_root / "razrabotka_orkestra.html").read_text(encoding="utf-8")
    assert secret_pattern.search(env_text) is None
    assert secret_pattern.search(html_text) is None
