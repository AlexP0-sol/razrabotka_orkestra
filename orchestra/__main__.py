"""Module entrypoint allowing `python -m orchestra ...` (DoD-06)."""

from __future__ import annotations

from orchestra.interfaces.cli import app

if __name__ == "__main__":
    app()
