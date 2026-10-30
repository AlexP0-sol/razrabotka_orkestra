"""Shared pytest fixtures for ORCHESTRA test suites."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root path."""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent
