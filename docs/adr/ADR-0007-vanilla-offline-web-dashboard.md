# ADR-0007: Zero-Build Offline Vanilla JS + Vendored Cytoscape Web Dashboard

- **ID:** ADR-0007
- **Date:** 2026-10-30
- **Status:** Accepted (`D-03`, `WB-01..08`)

## Context

The operator needs real-time visibility into the Mental Map DAG, per-agent reasoning streams, key
pool health, file diffs, RPC logs, token budgets, and human escalations — without installing Node.js,
running frontend build steps, or relying on external CDNs.

## Decision

Serve a static vanilla ES-module frontend (`orchestra/web/{index.html,app.js,styles.css}`) directly
from the FastAPI core (`api_server.py`) alongside a locally vendored graph engine in
`orchestra/web/vendor/`, backed by documented REST endpoints (`WB-02`) and a sequence-numbered
resumable WebSocket protocol (`/ws`, `after_seq=N`, `WB-03`).

## Rationale

Zero Node.js toolchain friction on Windows, instant startup via `make dev`, 100% offline operation,
and clean separation of API contracts so a heavier SPA can replace `web/` in future versions without
touching the Python core.

## Alternatives Considered

- React / Next.js + React Flow: deferred out of v1 scope by explicit customer decision (`D-03`).
- CDN-hosted scripts: forbidden by `WB-05` (offline requirement).

## Consequences

- Client-side event rendering uses `requestAnimationFrame` batching and caps DOM history at
  `WS_MAX_DOM_EVENTS = 2000` (`WB-08`).
