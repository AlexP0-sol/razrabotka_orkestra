# ADR-0008: Pluggable Crypto-Trading Domain Pack & Day-One Multi-Exchange Architecture

- **ID:** ADR-0008
- **Date:** 2026-10-30
- **Status:** Accepted (`Часть VIII`, `§38.19`, `D-11..D-15`, `D-18`, `D-19`)

## Context

The first production system built by ORCHESTRA is an algorithmic crypto-trading platform spanning
multiple exchanges whose exact live roster will be chosen later. Bugs in trading software risk real
capital due to `float` rounding, clock skew, dropped WebSocket deltas, duplicate fills, or blind
order retries after network timeouts.

## Decision

1. Define a generic `DomainPack` protocol (`orchestra/domains/base.py`) and implement
   `CryptoTradingDomainPack` (`orchestra/domains/crypto_trading.py`) supplying 8 default talent
   profiles, 12 executable domain skills with unit tests, 13 AST verifier rules (`crypto_ast`),
   mandatory human escalation gates (`DP-69..71`), and a canonical 34-node project decomposition.
2. Mandate day-one multi-exchange isolation (`DP-78..88`): `exchanges.yaml` data registry with
   fail-closed `enabled: []`, `ExchangeCapabilities`, normalized `BASE/QUOTE:SETTLE` models, `ccxt`
   as baseline adapter (`DP-81`) with optional native overrides, per-exchange rate limiters/clock
   skew/reconciliation/kill-switches, and `CrossExchangeTimeAligner`.
3. Enforce mechanical AST verification (`NO_FLOAT_MONEY`, `NO_NAIVE_DATETIME`,
   `NO_WALLCLOCK_IN_STRATEGY`, `NO_DIRECT_EXCHANGE_IMPORT`, `NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER`,
   `LIVE_MODE_GUARD`, etc.) as part of `DeterministicJudge`.

## Rationale

Keeps ORCHESTRA's core domain-agnostic while guaranteeing hard financial safety invariants (`TR-01..20`)
through static AST analysis, property tests, and chaos fixtures rather than prompt reminders alone.

## Alternatives Considered

- Hardcoding a single exchange (e.g., Binance only): rejected per customer directive ("exchanges
  will be many", `§38.19`).
- Native SDKs only without `ccxt` baseline: rejected for v1 baseline due to N× maintenance cost,
  while preserving `ExchangeAdapter` protocol for low-latency native overrides (`DP-76`, `DP-81`).

## Consequences

- Adding a new exchange touches only `gateway/adapters/` and `exchanges.yaml` (`DoD-49`).
- Default trading mode is strictly `PAPER` (`DP-06`).
