"""CLI wrapper in `scripts/` delegating to root `setup_env` interactive `.env` wizard."""

from __future__ import annotations

from pathlib import Path

from orchestra.config.setup_wizard import run_setup_wizard


def main() -> int:
    """Run interactive `.env` setup wizard."""
    run_setup_wizard(env_path=Path(".env"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
