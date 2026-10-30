"""Static AST security scanner for executable Python skills before registration (SEC-04, SK-05)."""

from __future__ import annotations

import ast

from orchestra.errors import SkillSecurityError

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "ctypes",
        "cffi",
        "pty",
        "resource",
        "socket",
        "http.client",
        "urllib.request",
        "ftplib",
        "telnetlib",
        "smtplib",
        "pickle",
        "marshal",
        "webbrowser",
    }
)

FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "os.system",
        "os.popen",
        "os.spawnl",
        "os.execl",
    }
)


def scan_skill_source(source_code: str, *, skill_id: str = "<candidate>") -> None:
    """Parse `source_code` with `ast` and reject unsafe imports, dynamic exec, or shell=True.

    Raises:
        SkillSecurityError: If syntax is invalid or any forbidden construct is detected.
    """
    try:
        tree = ast.parse(source_code, filename=skill_id)
    except SyntaxError as exc:
        raise SkillSecurityError(f"Syntax error in skill '{skill_id}': {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module_name(alias.name, skill_id=skill_id)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _check_module_name(node.module, skill_id=skill_id)
        elif isinstance(node, ast.Call):
            dotted = _dotted_call_name(node.func)
            if dotted in FORBIDDEN_CALLS:
                raise SkillSecurityError(
                    f"Forbidden dynamic execution call '{dotted}' in skill '{skill_id}'"
                )
            if dotted and dotted.startswith("subprocess."):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and bool(kw.value.value)
                    ):
                        raise SkillSecurityError(
                            f"Forbidden subprocess call with shell=True in skill '{skill_id}'"
                        )


def _check_module_name(mod_name: str, *, skill_id: str) -> None:
    for banned in FORBIDDEN_MODULES:
        if mod_name == banned or mod_name.startswith(f"{banned}."):
            raise SkillSecurityError(
                f"Forbidden module import '{mod_name}' in skill '{skill_id}' (SEC-04)"
            )


def _dotted_call_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        prefix = _dotted_call_name(expr.value)
        if prefix:
            return f"{prefix}.{expr.attr}"
    return None
