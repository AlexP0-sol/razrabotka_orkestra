"""CI & pre-commit secret leak scanner across workspace, logs, and git commits (T-06, DoD-44)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NVIDIA_API_KEY", re.compile(r"nvapi-[A-Za-z0-9_-]{20,}")),
    ("OPENAI_SK_KEY", re.compile(r"sk-[A-Za-z0-9]{24,}")),
    ("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("PEM_PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)

ALLOWLIST_MARKERS: tuple[str, ...] = (
    "REDACTED",
    "nvapi-REDACTED",
    "nvapi-xxxx",
    "nvapi-key1",
    "nvapi-key2",
    "nvapi-test",
    "sk-REDACTED",
    "123456789:AAExample",
)

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
    }
)

BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".whl", ".tar", ".gz", ".zip"}
)


def _is_allowlisted(matched_token: str, full_line: str) -> bool:
    return any(marker in matched_token or marker in full_line for marker in ALLOWLIST_MARKERS)


def scan_text(
    text: str,
    *,
    source_label: str,
    extra_exact_secrets: tuple[str, ...] = (),
) -> list[str]:
    """Scan text line-by-line and return violation strings without echoing raw secrets."""
    findings: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for exact in extra_exact_secrets:
            if len(exact) >= 8 and exact in line:
                findings.append(f"{source_label}:{line_no}: exact secret value leaked")
        for rule_name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                token = match.group(0)
                if not _is_allowlisted(token, line):
                    findings.append(f"{source_label}:{line_no}: {rule_name} pattern matched")
    return findings


def scan_directory(
    root: Path,
    *,
    extra_exact_secrets: tuple[str, ...] = (),
) -> list[str]:
    """Recursively scan all non-ignored text files under `root`."""
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root).as_posix()
        findings.extend(
            scan_text(content, source_label=rel, extra_exact_secrets=extra_exact_secrets)
        )
    return findings


def scan_git_commits(
    repo_dir: Path,
    *,
    max_commits: int = 50,
    extra_exact_secrets: tuple[str, ...] = (),
    strict_full_history: bool = False,
) -> list[str]:
    """Scan git commit messages, tracked HEAD blobs, and (when strict) full commit diffs."""
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        return []

    findings: list[str] = []

    # 1. Always scan commit messages across recent history
    msg_proc = subprocess.run(
        ["git", "log", f"-n{max_commits}", "--format=%H %B"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if msg_proc.returncode == 0:
        findings.extend(
            scan_text(
                msg_proc.stdout,
                source_label="git_commit_messages",
                extra_exact_secrets=extra_exact_secrets,
            )
        )

    # 2. Scan latest HEAD patch additions and full diff history for generated E2E workspaces
    log_args = (
        ["git", "log", "-p", f"-n{max_commits}"]
        if (strict_full_history or bool(extra_exact_secrets))
        else ["git", "show", "--format=%B", "-p", "HEAD"]
    )
    proc = subprocess.run(
        log_args,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return findings

    for idx, line in enumerate(proc.stdout.splitlines(), start=1):
        if line.startswith(("---", "+++", "-")):
            continue
        for exact in extra_exact_secrets:
            if len(exact) >= 8 and exact in line:
                findings.append(f"git_log:{idx}: exact secret leaked in git commit")
        if line.startswith("+"):
            for rule_name, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(line):
                    if not _is_allowlisted(match.group(0), line):
                        findings.append(f"git_log:{idx}: {rule_name} in git commit")
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint returning exit code 0 when clean or 1 on any secret leak."""
    parser = argparse.ArgumentParser(description="Scan repository and runtime dirs for secrets.")
    parser.add_argument("paths", nargs="*", default=["."], help="Directories to scan.")
    parser.add_argument("--no-git", action="store_true", help="Skip git history scan.")
    args = parser.parse_args(argv)

    all_findings: list[str] = []
    for raw_path in args.paths:
        target = Path(raw_path).resolve()
        if not target.exists():
            continue
        all_findings.extend(scan_directory(target))
        if not args.no_git:
            all_findings.extend(scan_git_commits(target))

    if all_findings:
        for item in all_findings:
            sys.stderr.write(f"SECRET LEAK: {item}\n")
        return 1

    sys.stdout.write("Secret leak scan PASSED: 0 findings.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
