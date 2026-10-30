# ADR-0009: Repository Root Layout Reconciliation (Spec v1.2.1)

- **ID:** ADR-0009
- **Date:** 2026-10-30
- **Status:** Accepted (`v1.2.1`, `FP-15`, `DoD-01..03`)

## Context

Audit of the previous agent's specification (`MASTER_PROMPT.md` v1.2 + PR #1 Codex Review) identified
a root-path discrepancy: `§0` declared the repository root as the project root (`make install`,
`python -m orchestra doctor`), whereas `§10` wrapped the tree inside an extra `orchestra/` directory
(`orchestra/orchestra/`) and `FP-15` conflicted with `DoD-36` regarding root `README.md` and `prompts/`.

## Decision

1. Align the project tree directly at the repository root `./` (`./pyproject.toml`, `./Makefile`,
   `./orchestra/`, `./tests/`, `./docs/`, `./scripts/`).
2. Preserve `./prompts/MASTER_PROMPT.md`, `./prompts/SOURCE_ANALYSIS.md`, and sanitized
   `./razrabotka_orkestra.html` in place (`FP-15`), while placing runtime system prompt templates
   strictly under `./orchestra/prompts/templates/`.
3. Map the `>= 85%` core test coverage requirement (`M-03`, `TT-05`) to the concrete core packages
   `orchestra/{state,verify,inference,swarm,agents,comms}`.

## Rationale

Ensures every command in `README.md`, `Makefile`, `Dockerfile`, CI, and `Definition of Done` works
directly from the repository root without nested path ambiguity.

## Alternatives Considered

- Nesting the entire project inside `./orchestra/`: rejected because root-level `make`, `pytest`,
  and `python -m orchestra` invocations in `DoD-02..08` would fail from the repo root.

## Consequences

- `MASTER_PROMPT.md` updated to `v1.2.1` with synchronized module names, 10 builtin skills, 12 crypto
  skills, 10 prompt templates, 13 domain verifier rules, and corrected `DP-xx` cross-references.
