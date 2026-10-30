"""AST verifier implementing all 13 `crypto_trading` rules from §38.13 (DP-57..59, DoD-40)."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from orchestra.verify.verifiers import VerifierFinding

if TYPE_CHECKING:
    from pathlib import Path

CRYPTO_VERIFIER_RULES: tuple[str, ...] = (
    "NO_FLOAT_MONEY",
    "NO_NAIVE_DATETIME",
    "NO_WALLCLOCK_IN_STRATEGY",
    "NO_DIRECT_EXCHANGE_IMPORT",
    "NO_LOOKAHEAD",
    "MANDATORY_CLIENT_ORDER_ID",
    "ORDER_AUDIT_REQUIRED",
    "NO_UNBOUNDED_AVERAGING",
    "DECIMAL_JSON_SAFETY",
    "SECRET_NOT_LITERAL",
    "LIVE_MODE_GUARD",
    "NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER",
    "NO_FLOAT_CROSS_EXCHANGE",
)

_MONEY_KEYWORDS = (
    "price",
    "qty",
    "quantity",
    "amount",
    "balance",
    "pnl",
    "fee",
    "commission",
    "notional",
    "margin",
)
_CROSS_EXCHANGE_KEYWORDS = ("cross_exchange", "total_pnl", "aggregated_pnl", "portfolio_pnl")
_EXCHANGE_SDK_MODULES = frozenset({"ccxt", "binance", "bybit", "okx", "kraken", "bitget", "kucoin"})
_EXCHANGE_SPECIFIC_RE = re.compile(
    r"(?i)\b(?:X-MBX-USED-WEIGHT|/fapi/v1|/api/v5/trade|BINA-API|OK-ACCESS-SIGN)\b"
)
_EXCHANGE_SECRET_LITERAL_RE = re.compile(r"(?i)^(?:api[_-]?secret|secret[_-]?key|private[_-]?key)$")


def _is_money_ident(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in _MONEY_KEYWORDS)


def _is_cross_exchange_ident(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in _CROSS_EXCHANGE_KEYWORDS)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        prefix = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
        return f"{prefix}.{node.func.attr}" if prefix else node.func.attr
    return ""


def _kw_names(node: ast.Call) -> set[str]:
    return {kw.arg for kw in node.keywords if kw.arg is not None}


def scan_crypto_python_source(source: str, *, rel_path: str) -> list[VerifierFinding]:
    """Run all 13 `crypto_trading` AST/static checks over `source` at `rel_path`."""
    norm_path = rel_path.replace("\\", "/")
    is_gateway = "gateway" in norm_path
    is_adapter = "gateway/adapters/" in norm_path or norm_path.endswith("exchanges.yaml")
    is_strategy = "strategy" in norm_path or "strategies" in norm_path
    is_backtest = "backtest" in norm_path

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            VerifierFinding(
                verifier="crypto_ast",
                file_path=rel_path,
                line=exc.lineno or 1,
                code="SYNTAX_ERROR",
                message=f"SyntaxError: {exc.msg}",
            )
        ]

    findings: list[VerifierFinding] = []
    has_audit_ref = "audit" in source.lower()

    # Rule 12: NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER
    if not is_adapter and _EXCHANGE_SPECIFIC_RE.search(source):
        findings.append(
            VerifierFinding(
                verifier="crypto_ast",
                file_path=rel_path,
                line=1,
                code="NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER",
                message="Exchange-specific endpoint/header leaked outside gateway/adapters/",
            )
        )

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", 1)

        # Rule 4: NO_DIRECT_EXCHANGE_IMPORT
        if isinstance(node, ast.Import) and not is_gateway:
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _EXCHANGE_SDK_MODULES:
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="NO_DIRECT_EXCHANGE_IMPORT",
                            message=f"Exchange SDK '{root_mod}' imported outside gateway layer",
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and node.module and not is_gateway:
            root_mod = node.module.split(".")[0]
            if root_mod in _EXCHANGE_SDK_MODULES:
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code="NO_DIRECT_EXCHANGE_IMPORT",
                        message=f"Exchange SDK '{root_mod}' imported outside gateway layer",
                    )
                )

        # Rule 1 & Rule 13: NO_FLOAT_MONEY & NO_FLOAT_CROSS_EXCHANGE
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ident = node.target.id
            ann_str = ast.unparse(node.annotation)
            if "float" in ann_str:
                if _is_cross_exchange_ident(ident):
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="NO_FLOAT_CROSS_EXCHANGE",
                            message=f"Cross-exchange metric '{ident}' typed as float (DP-83)",
                        )
                    )
                elif _is_money_ident(ident):
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="NO_FLOAT_MONEY",
                            message=f"Monetary variable '{ident}' annotated with float (TR-01)",
                        )
                    )
            if (
                node.value is not None
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, float)
                and (_is_money_ident(ident) or _is_cross_exchange_ident(ident))
            ):
                code = (
                    "NO_FLOAT_CROSS_EXCHANGE"
                    if _is_cross_exchange_ident(ident)
                    else "NO_FLOAT_MONEY"
                )
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code=code,
                        message=f"Float literal assigned to '{ident}'",
                    )
                )

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    ident = target.id
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, float):
                        if _is_cross_exchange_ident(ident):
                            findings.append(
                                VerifierFinding(
                                    verifier="crypto_ast",
                                    file_path=rel_path,
                                    line=lineno,
                                    code="NO_FLOAT_CROSS_EXCHANGE",
                                    message=f"Float literal in cross-exchange '{ident}' (DP-83)",
                                )
                            )
                        elif _is_money_ident(ident):
                            findings.append(
                                VerifierFinding(
                                    verifier="crypto_ast",
                                    file_path=rel_path,
                                    line=lineno,
                                    code="NO_FLOAT_MONEY",
                                    message=f"Float literal in monetary '{ident}' (TR-01)",
                                )
                            )
                    # Rule 10: SECRET_NOT_LITERAL
                    if (
                        _EXCHANGE_SECRET_LITERAL_RE.match(ident)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and len(node.value.value) >= 8
                    ):
                        findings.append(
                            VerifierFinding(
                                verifier="crypto_ast",
                                file_path=rel_path,
                                line=lineno,
                                code="SECRET_NOT_LITERAL",
                                message=f"Hardcoded secret string literal in '{ident}'",
                            )
                        )
                    # Rule 8: NO_UNBOUNDED_AVERAGING
                    if (
                        ("martingale" in ident.lower() or "averaging_multiplier" in ident.lower())
                        and "max_position" not in source
                        and "risk_limit" not in source
                    ):
                        findings.append(
                            VerifierFinding(
                                verifier="crypto_ast",
                                file_path=rel_path,
                                line=lineno,
                                code="NO_UNBOUNDED_AVERAGING",
                                message="Martingale / averaging without risk_limit (DP-39)",
                            )
                        )

        # Rule 5: NO_LOOKAHEAD (future negative-shift or raw future slice in backtest/strategy)
        if (is_backtest or is_strategy) and isinstance(node, ast.Call):
            cname = _call_name(node)
            if cname.endswith("shift") and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.UnaryOp) and isinstance(arg0.op, ast.USub):
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="NO_LOOKAHEAD",
                            message="Negative shift(-N) accesses future bars (TR-15, DP-43)",
                        )
                    )
            if cname.endswith("get_bars") and "as_of" not in _kw_names(node):
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code="NO_LOOKAHEAD",
                        message="get_bars() called without mandatory as_of boundary (DP-43)",
                    )
                )

        if isinstance(node, ast.Call):
            cname = _call_name(node)
            kws = _kw_names(node)

            # Rule 2: NO_NAIVE_DATETIME
            if cname in ("datetime.utcnow", "utcnow"):
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code="NO_NAIVE_DATETIME",
                        message="datetime.utcnow() produces naive datetime; use datetime.now(UTC)",
                    )
                )
            elif (
                cname in ("datetime.now", "datetime.fromtimestamp")
                and not node.args
                and "tz" not in kws
            ):
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code="NO_NAIVE_DATETIME",
                        message=f"{cname}() called without timezone argument (TR-02)",
                    )
                )

            # Rule 3: NO_WALLCLOCK_IN_STRATEGY
            if is_strategy and cname in ("time.time", "time.monotonic", "asyncio.sleep"):
                findings.append(
                    VerifierFinding(
                        verifier="crypto_ast",
                        file_path=rel_path,
                        line=lineno,
                        code="NO_WALLCLOCK_IN_STRATEGY",
                        message=f"Wall-clock call '{cname}' forbidden inside strategy (DP-19)",
                    )
                )

            # Rule 6 & Rule 7: MANDATORY_CLIENT_ORDER_ID & ORDER_AUDIT_REQUIRED
            if cname.endswith(("submit_order", "create_order", "place_order")):
                if not ({"client_order_id", "clientOrderId"} & kws):
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="MANDATORY_CLIENT_ORDER_ID",
                            message=f"{cname}() missing client_order_id / clientOrderId (TR-03)",
                        )
                    )
                if not has_audit_ref:
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="ORDER_AUDIT_REQUIRED",
                            message=f"{cname}() called without append-only audit log (TR-04)",
                        )
                    )

            # Rule 9: DECIMAL_JSON_SAFETY (`float(dec)` or `json.dumps(..., default=float)`)
            if cname == "float" and node.args and isinstance(node.args[0], ast.Name):
                arg_id = node.args[0].id
                if _is_money_ident(arg_id) or "dec" in arg_id.lower():
                    findings.append(
                        VerifierFinding(
                            verifier="crypto_ast",
                            file_path=rel_path,
                            line=lineno,
                            code="DECIMAL_JSON_SAFETY",
                            message=f"Converting '{arg_id}' via float() loses precision (DP-16)",
                        )
                    )
            if cname == "json.dumps":
                for kw in node.keywords:
                    if (
                        kw.arg == "default"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id == "float"
                    ):
                        findings.append(
                            VerifierFinding(
                                verifier="crypto_ast",
                                file_path=rel_path,
                                line=lineno,
                                code="DECIMAL_JSON_SAFETY",
                                message="json.dumps(default=float) forbidden for Decimal (DP-16)",
                            )
                        )

        # Rule 11: LIVE_MODE_GUARD
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "TradingMode"
            and node.attr == "LIVE"
            and "human_confirmed_live" not in source
            and "assert_live_mode_authorized" not in source
        ):
            findings.append(
                VerifierFinding(
                    verifier="crypto_ast",
                    file_path=rel_path,
                    line=lineno,
                    code="LIVE_MODE_GUARD",
                    message="TradingMode.LIVE without human_confirmed_live guard (DP-71)",
                )
            )

    return findings


def verify_crypto_domain_files(
    workspace_root: Path,
    rel_paths: list[str],
) -> list[VerifierFinding]:
    """Run all 13 `crypto_trading` AST checks across `rel_paths` under `workspace_root`."""
    all_findings: list[VerifierFinding] = []
    for rel in rel_paths:
        if not rel.endswith(".py"):
            continue
        target = workspace_root / rel
        if not target.is_file():
            continue
        src = target.read_text(encoding="utf-8")
        all_findings.extend(scan_crypto_python_source(src, rel_path=rel))
    return all_findings
