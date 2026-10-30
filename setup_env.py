"""Интерактивный мастер настройки ORCHESTRA: поочерёдный ввод модели, ключей и параметров."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def _find_venv_python() -> Path | None:
    """Locate `.venv` Python interpreter on Windows or POSIX if present."""
    candidates = (
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    """Run interactive `.env` setup wizard (using `.venv` automatically when available)."""
    venv_dir = REPO_ROOT / ".venv"
    venv_py = _find_venv_python()
    if (
        venv_py is not None
        and venv_dir.is_dir()
        and Path(sys.prefix).resolve() != venv_dir.resolve()
    ):
        proc = subprocess.run([str(venv_py), str(Path(__file__).resolve())], check=False)
        return proc.returncode

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from orchestra.config.setup_wizard import run_setup_wizard

    env_target = Path(os.environ.get("ORCHESTRA_ENV_FILE", str(REPO_ROOT / ".env")))
    try:
        run_setup_wizard(env_path=env_target)
    except (KeyboardInterrupt, EOFError):
        sys.stdout.write("\nНастройка прервана пользователем. Изменения не сохранены.\n")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
