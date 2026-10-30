"""Decimal monetary types, multi-exchange capabilities, and per-exchange isolation (§38.2-38.19)."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from orchestra.errors import OrchestraError


class AssetMismatchError(OrchestraError):
    """Raised when combining `Amount` instances denominated in different assets (DP-12, TR-20)."""


class LiveModeConfirmationError(OrchestraError):
    """Raised when entering `LIVE` trading without explicit human confirmation (TR-07)."""


class TradingMode(StrEnum):
    """Execution mode enum with `PAPER` default and gated `LIVE` activation (DP-06, DP-38)."""

    SHADOW = "SHADOW"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


def parse_decimal(raw: str | int | Decimal) -> Decimal:
    """Parse monetary value strictly without float conversion (DP-16, TR-01)."""
    if isinstance(raw, float):
        raise TypeError("float is forbidden in parse_decimal (TR-01); pass str or Decimal")
    if isinstance(raw, Decimal):
        return raw
    try:
        with localcontext() as ctx:
            ctx.prec = 28
            return Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid Decimal literal: {raw!r}") from exc


def dump_decimal(value: Decimal) -> str:
    """Serialize `Decimal` deterministically as plain string without scientific float drift."""
    normalized = format(value.normalize(), "f")
    return normalized if normalized != "-0" else "0"


def quantize_down(value: Decimal, quantum: Decimal) -> Decimal:
    """Quantize `value` down (`ROUND_DOWN`) to exchange `tick_size` / `step_size` (DP-14, TR-17)."""
    with localcontext() as ctx:
        ctx.prec = 28
        return value.quantize(quantum, rounding=ROUND_DOWN)


class Amount(BaseModel):
    """Currency-tagged `Decimal` preventing silent cross-asset arithmetic (TR-20, DP-12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Decimal
    asset: str = Field(min_length=1)

    def _check_same_asset(self, other: Amount) -> None:
        if self.asset != other.asset:
            raise AssetMismatchError(
                f"Cannot combine different assets: {self.asset} vs {other.asset}"
            )

    def __add__(self, other: Amount) -> Amount:
        """Add two `Amount` values of identical `asset`."""
        self._check_same_asset(other)
        with localcontext() as ctx:
            ctx.prec = 28
            return Amount(value=self.value + other.value, asset=self.asset)

    def __sub__(self, other: Amount) -> Amount:
        """Subtract two `Amount` values of identical `asset`."""
        self._check_same_asset(other)
        with localcontext() as ctx:
            ctx.prec = 28
            return Amount(value=self.value - other.value, asset=self.asset)


class ExchangeCapabilities(BaseModel):
    """Declarative capability descriptor for an exchange in `exchanges.yaml` (DP-79, DP-86)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    exchange_id: str
    adapter: str = "ccxt_baseline"
    native_override: str | None = None
    enabled: bool = False
    markets: tuple[str, ...] = ("spot",)
    order_types: tuple[str, ...] = ("LIMIT", "MARKET")
    signing: tuple[str, ...] = ("HMAC-SHA256",)
    supports_testnet: bool = True
    supports_post_only: bool = True
    supports_reduce_only: bool = True
    orderbook_sync_mode: str = "last_update_id_continuity"
    client_order_id_max_len: int = 36
    rate_limit_requests_per_min: int = 1200
    ws_silence_timeout_s: int = 30
    funding_interval_hours: int | None = 8
    timeframes: tuple[str, ...] = ("1m", "5m", "1h", "1d")
    max_open_orders_per_symbol: int = 200


class ExchangeAdapter(Protocol):
    """Unified multi-exchange adapter protocol (DP-78)."""

    @property
    def capabilities(self) -> ExchangeCapabilities:
        """Return static/probed capabilities of this exchange."""
        ...


class ExchangeTokenBucket:
    """Isolated per-exchange weighted rate limiter (DP-21, DP-82)."""

    def __init__(self, capacity: int) -> None:
        """Initialize token bucket with `capacity` weight units."""
        self.capacity = max(1, capacity)
        self.available = float(self.capacity)
        self.halted_ip_ban = False

    def consume(self, weight: int = 1) -> bool:
        """Attempt to spend `weight` units; return False when throttled or IP-banned."""
        if self.halted_ip_ban or weight > self.available:
            return False
        self.available -= weight
        return True

    def on_http_status(self, status_code: int) -> str:
        """Handle 429 backoff vs 418 immediate halt (DP-22)."""
        if status_code == 418:
            self.halted_ip_ban = True
            self.available = 0.0
            return "HALT_IP_BAN"
        if status_code == 429:
            self.available = 0.0
            return "BACKOFF_429"
        return "OK"


class ExchangeKillSwitch:
    """Per-exchange fail-closed kill switch (TR-06, DP-36, DP-82)."""

    def __init__(self, exchange_id: str) -> None:
        """Initialize unarmed kill switch for `exchange_id`."""
        self.exchange_id = exchange_id
        self.halted = False
        self.reason: str | None = None

    def trip(self, reason: str) -> None:
        """Halt trading on this exchange and record `reason`."""
        self.halted = True
        self.reason = reason


class PerExchangeRuntimeContext:
    """Strictly isolated limiter, clock skew, and kill switch per exchange (DP-82)."""

    def __init__(
        self, capabilities: ExchangeCapabilities, *, clock_skew_limit_ms: int = 1000
    ) -> None:
        """Initialize isolated runtime controls for a single exchange."""
        self.capabilities = capabilities
        self.rate_limiter = ExchangeTokenBucket(capabilities.rate_limit_requests_per_min)
        self.kill_switch = ExchangeKillSwitch(capabilities.exchange_id)
        self.clock_skew_ms: float = 0.0
        self._skew_limit_ms = clock_skew_limit_ms

    def update_clock_skew(self, *, server_ms: int, local_ms: int, rtt_ms: int) -> float:
        """Update RTT-adjusted clock skew and trip kill switch if above limit (DP-17..18)."""
        skew = float(server_ms) - (float(local_ms) + float(rtt_ms) / 2.0)
        self.clock_skew_ms = round(skew, 2)
        if abs(self.clock_skew_ms) > self._skew_limit_ms:
            self.kill_switch.trip(f"CLOCK_SKEW_HIGH:{self.clock_skew_ms}ms")
        return self.clock_skew_ms


class ExchangeRegistry:
    """Data-driven multi-exchange registry loaded from `exchanges.yaml` (DP-78, DP-88, DoD-49)."""

    def __init__(self, capabilities_by_id: dict[str, ExchangeCapabilities]) -> None:
        """Initialize registry and build isolated runtime contexts per exchange."""
        self._caps = dict(capabilities_by_id)
        self._contexts = {eid: PerExchangeRuntimeContext(cap) for eid, cap in self._caps.items()}

    @classmethod
    def _normalize_entry(
        cls,
        eid: str,
        raw_cfg: dict[str, Any],
        *,
        is_enabled: bool,
    ) -> ExchangeCapabilities:
        cfg = dict(raw_cfg)
        raw_rl = cfg.get("rate_limit")
        rl: dict[str, Any] = dict(raw_rl) if isinstance(raw_rl, dict) else {}
        raw_ws = cfg.get("ws")
        ws: dict[str, Any] = dict(raw_ws) if isinstance(raw_ws, dict) else {}
        raw_fn = cfg.get("funding")
        fn: dict[str, Any] = dict(raw_fn) if isinstance(raw_fn, dict) else {}
        raw_sig = cfg.get("signing", ["HMAC-SHA256"])
        sig_tuple = (
            tuple(str(x) for x in raw_sig) if isinstance(raw_sig, list | tuple) else (str(raw_sig),)
        )
        order_types = tuple(str(x) for x in cfg.get("order_types", ("LIMIT", "MARKET")))
        return ExchangeCapabilities(
            exchange_id=eid,
            adapter=str(cfg.get("adapter", cfg.get("adapter_type", "ccxt_baseline"))),
            native_override=cfg.get("native_override") or cfg.get("adapter_module"),
            enabled=bool(cfg.get("enabled", False)) or is_enabled,
            markets=tuple(str(x) for x in cfg.get("markets", ("spot",))),
            order_types=order_types,
            signing=sig_tuple,
            supports_testnet=bool(cfg.get("has_testnet", cfg.get("supports_testnet", True))),
            supports_post_only="POST_ONLY" in order_types,
            supports_reduce_only=bool(cfg.get("supports_reduce_only", True)),
            orderbook_sync_mode=str(
                ws.get("book_resync_rule", cfg.get("orderbook_sync_mode", "lastUpdateId"))
            ),
            client_order_id_max_len=int(cfg.get("client_order_id_max_len", 36)),
            rate_limit_requests_per_min=int(
                rl.get("requests_per_minute", cfg.get("rate_limit_requests_per_min", 1200))
            ),
            ws_silence_timeout_s=int(
                ws.get("silence_timeout_s", cfg.get("ws_silence_timeout_s", 30))
            ),
            funding_interval_hours=fn.get(
                "interval_hours",
                cfg.get("funding_interval_hours", 8),
            ),
            timeframes=tuple(str(x) for x in cfg.get("timeframes", ("1m", "1h", "1d"))),
            max_open_orders_per_symbol=int(cfg.get("max_open_orders_per_symbol", 200)),
        )

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> ExchangeRegistry:
        """Load `exchanges.yaml` supporting nested capabilities and fail-closed `enabled`."""
        raw: Any = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        enabled_set = set(raw.get("enabled", [])) if isinstance(raw, dict) else set()
        raw_exchanges = raw.get("exchanges", {}) if isinstance(raw, dict) else {}
        parsed: dict[str, ExchangeCapabilities] = {}
        if isinstance(raw_exchanges, dict):
            for eid, cfg in raw_exchanges.items():
                parsed[str(eid)] = cls._normalize_entry(
                    str(eid),
                    dict(cfg or {}),
                    is_enabled=(eid in enabled_set),
                )
        elif isinstance(raw_exchanges, list):
            for entry in raw_exchanges:
                item = dict(entry)
                eid = str(item["exchange_id"])
                parsed[eid] = cls._normalize_entry(
                    eid,
                    item,
                    is_enabled=(eid in enabled_set),
                )
        return cls(parsed)

    def all_exchanges(self) -> list[ExchangeCapabilities]:
        """Return all configured exchanges sorted by ID."""
        return [self._caps[k] for k in sorted(self._caps)]

    def enabled_exchanges(self) -> list[ExchangeCapabilities]:
        """Return only explicitly enabled exchanges (empty by default = fail-closed, DP-88)."""
        return [c for c in self.all_exchanges() if c.enabled]

    def context_for(self, exchange_id: str) -> PerExchangeRuntimeContext:
        """Return isolated runtime context (limiter, skew, kill switch) for `exchange_id`."""
        return self._contexts[exchange_id]


class CrossExchangeTimeAligner:
    """Aligns multi-exchange market events by `exchange_ts` within `tolerance_ms` (DP-85)."""

    def __init__(self, *, tolerance_ms: int = 50) -> None:
        """Initialize aligner with explicit millisecond tolerance."""
        self.tolerance_ms = max(0, tolerance_ms)

    def are_aligned(self, ts_a: datetime, ts_b: datetime) -> bool:
        """Return True iff timezone-aware UTC timestamps differ by at most `tolerance_ms`."""
        if ts_a.tzinfo is None or ts_b.tzinfo is None:
            raise ValueError("Naive datetime forbidden in CrossExchangeTimeAligner (TR-02)")
        delta_ms = abs((ts_a - ts_b).total_seconds() * 1000.0)
        return delta_ms <= self.tolerance_ms


def assert_live_mode_authorized(
    mode: TradingMode, *, human_confirmed_live: bool, confirmation_text: str = ""
) -> None:
    """Enforce DP-06 / DP-71 / TR-07: `LIVE` mode requires explicit human `'LIVE'` confirmation."""
    if mode == TradingMode.LIVE and (
        not human_confirmed_live or confirmation_text.strip() != "LIVE"
    ):
        raise LiveModeConfirmationError(
            "LIVE mode blocked: requires human_confirmed_live=True and text='LIVE' (DP-71)."
        )
