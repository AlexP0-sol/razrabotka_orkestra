"""Out-of-the-box offline demo runner on MockProvider without API keys (§10, DoD-37)."""

from __future__ import annotations

import argparse
from pathlib import Path

from orchestra.interfaces.cli import cmd_demo


def main(argv: list[str] | None = None) -> int:
    """Seed domain skills/talents and run the deterministic MockProvider demo pipeline."""
    parser = argparse.ArgumentParser(description="Run ORCHESTRA offline MockProvider demo.")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path("./runtime/demo_bootstrap"),
        help="Runtime directory for demo artifacts.",
    )
    args = parser.parse_args(argv)
    cmd_demo(runtime_dir=args.runtime_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
