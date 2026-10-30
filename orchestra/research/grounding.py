"""Runtime package & signature grounding against hallucinated library APIs (§13.14, RS-05..09)."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import inspect
from typing import Any

from pydantic import BaseModel, ConfigDict

from orchestra.errors import HallucinatedApiError


class ApiSymbolFact(BaseModel):
    """Grounded signature and package version inspected directly from Python runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_name: str
    package_version: str
    qualified_name: str
    kind: str
    signature_str: str
    docstring_head: str = ""


class GroundingInspector:
    """Inspects live installed packages via `importlib` + `inspect` for ground-truth APIs."""

    def get_package_version(self, dist_name: str) -> str | None:
        """Return installed distribution version or `None` if absent."""
        try:
            return importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def inspect_symbol(self, qualified_name: str) -> ApiSymbolFact:
        """Inspect `module.attr` (or `module.Class.method`) and return grounded `ApiSymbolFact`.

        Raises:
            HallucinatedApiError: When module or attribute does not exist in runtime.
        """
        parts = [p for p in qualified_name.strip().split(".") if p]
        if len(parts) < 2:
            raise HallucinatedApiError(
                f"Expected dotted 'module.symbol' path, got '{qualified_name}'"
            )

        mod: Any = None
        split_idx = len(parts) - 1
        while split_idx >= 1:
            candidate_mod = ".".join(parts[:split_idx])
            try:
                mod = importlib.import_module(candidate_mod)
                break
            except Exception:
                split_idx -= 1

        if mod is None:
            raise HallucinatedApiError(
                f"Module for '{qualified_name}' is not installed in runtime (RS-08)"
            )

        obj: Any = mod
        for attr in parts[split_idx:]:
            if not hasattr(obj, attr):
                raise HallucinatedApiError(
                    f"Hallucinated symbol '{qualified_name}': '{attr}' does not exist (RS-08)"
                )
            obj = getattr(obj, attr)

        top_pkg = parts[0]
        pkg_ver = self.get_package_version(top_pkg) or "stdlib-or-local"
        kind = "class" if inspect.isclass(obj) else ("function" if callable(obj) else "value")
        try:
            sig_str = str(inspect.signature(obj)) if callable(obj) else repr(type(obj).__name__)
        except (TypeError, ValueError):
            sig_str = "(...)"
        doc = (inspect.getdoc(obj) or "").splitlines()
        doc_head = doc[0][:160] if doc else ""

        return ApiSymbolFact(
            package_name=top_pkg,
            package_version=pkg_ver,
            qualified_name=qualified_name,
            kind=kind,
            signature_str=sig_str,
            docstring_head=doc_head,
        )

    def verify_source_attribute_calls(
        self,
        source_code: str,
        *,
        local_roots: set[str] | None = None,
    ) -> list[str]:
        """Verify `import pkg; pkg.attr(...)` attribute accesses against installed modules."""
        tree = ast.parse(source_code)
        imported_aliases: dict[str, str] = {}
        local_prefixes = local_roots or set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    imported_aliases[bound] = target
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    imported_aliases[bound] = f"{node.module}.{alias.name}"

        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                base_alias = node.value.id
                if base_alias not in imported_aliases:
                    continue
                target_path = imported_aliases[base_alias]
                top = target_path.split(".")[0]
                if top in local_prefixes or top == "orchestra":
                    continue
                full_ref = f"{target_path}.{node.attr}"
                try:
                    self.inspect_symbol(full_ref)
                except HallucinatedApiError as exc:
                    violations.append(str(exc))
        return sorted(set(violations))
