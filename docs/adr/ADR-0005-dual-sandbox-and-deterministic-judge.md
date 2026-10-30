# ADR-0005: Dual-Mode Sandbox and Deterministic Compiler-First Judge

- **ID:** ADR-0005
- **Date:** 2026-10-30
- **Status:** Accepted (`C-13`, `D-04`, `§13.11`, `§18`)

## Context

LLM self-reviews frequently hallucinate code correctness while missing syntax errors, strict typing
mismatches, broken imports, or vacuous unit tests.

## Decision

1. Never execute agent-generated code inside the orchestrator process (`BAN-06`). Run all checks via
   `Sandbox` supporting `subprocess` (default) and `docker` (`--network none`), passing only a
   whitelisted environment free of orchestrator secrets (`SB-05`).
2. Enforce a deterministic verification pipeline (`DeterministicJudge`) ordered from cheapest to
   most expensive (`SB-14`): `ast.parse` → `ruff check` → `ruff format --check` → `mypy --strict`
   (+ domain AST verifiers when active) → `pytest -q`.
3. Add probabilistic mutation probing (`MUTATION_CHECK_PROBABILITY = 0.2`, `JG-05`) that inverts an
   assertion/operator to reject vacuous test suites (`VACUOUS_TESTS`).
4. Cap auto-repair loops at `MAX_VERIFY_ITERATIONS = 2` per atom (`≤ 300` lines) before rejecting/escalating.

## Rationale

Compiler, type-checker, and test-runner exit codes (`0`) provide objective, non-negotiable proof of
technical validity before any LLM acceptance token is spent.

## Alternatives Considered

- Docker-only sandbox: rejected because users without Docker Desktop installed must still have a
  working system out of the box (`WIN-20`).

## Consequences

- All tool outputs normalize into structured `Finding` objects (`SB-15`), saving context tokens.
