# ADR-0003: Single-Writer Snapshot + Append-Only Event Log Persistence

- **ID:** ADR-0003
- **Date:** 2026-10-30
- **Status:** Accepted (`C-11`, `D-01`, `MM-11..15`, `PS-01..05`)

## Context

Multiple concurrent agents (`MAX_CONCURRENT_AGENTS = 6`) read and trigger updates to the project's
Mental Map (SSOT), contracts, ADRs, and anti-pattern registry while the process may be terminated
at any instant (`SIGKILL` / `TerminateProcess`).

## Decision

1. Funnel all state mutations through a single-writer actor (`StateStore`) backed by an
   `asyncio.Queue` and monotonic `map_version` optimistic concurrency (`StaleStateError`).
2. Persist every state event immediately to an append-only `events.jsonl` log with monotonic `seq`.
3. Write full `mental_map.json` snapshots with canonical SHA-256 checksums via atomic replacement
   (`temp → flush → os.fsync → os.replace`, with Windows retry backoff) every
   `SNAPSHOT_EVERY_EVENTS = 200` events and on clean shutdown, retaining `SNAPSHOT_BACKUPS = 5`
   historical copies.
4. Recover on startup via `recovery_scan()`: load latest valid snapshot, replay `events.jsonl`
   entries with `seq > snapshot_seq`, expire stale node leases, and resume open human questions.

## Rationale

Zero external database setup is needed on a user's Windows PC, files remain human-inspectable,
partial writes are impossible (`INV-15`), and crash recovery is 100% deterministic (`INV-09`).

## Alternatives Considered

- External PostgreSQL/Redis for orchestrator state: rejected due to heavy local setup overhead.
- Direct concurrent file locking from agents: rejected (`BAN-13`), prone to deadlocks and Windows
  file-handle contention.

## Consequences

- Readers operate lock-free on immutable snapshots (`read_snapshot()`).
- Mutations must supply `expected_version` and retry cleanly on `StaleStateError`.
