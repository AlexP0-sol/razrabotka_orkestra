# ADR-0006: ASCII Telegraphic JSON-RPC Inside Swarm and Russian SITREP to Human

- **ID:** ADR-0006
- **Date:** 2026-10-30
- **Status:** Accepted (`C-07`, `§14`, `§19`, `§20`)

## Context

Conversational politeness wastes 30–40% of LLM tokens, pollutes context windows, and causes BPE
tokenization overhead when mixing scripts (Cyrillic/CJK) with ASCII identifiers and stack traces.
Conversely, the human customer requires concise, zero-fluff engineering reports in Russian.

## Decision

1. Enforce `INTER_AGENT_RPC_V1` (`RpcEnvelope`, `extra="forbid"`) for all machine-to-machine
   messages, validated mechanically by `telegraphic.py` (ASCII `0x20–0x7E` only, forbidden articles
   and politeness tokens, strict token caps `note <= 40`, max 1 query/response round before
   `DISAGREEMENT_TICKET`).
2. Route all human-facing communication exclusively through the Meta-Architect using a deterministic
   Russian `SITREP` template (`СТАТУС / ВОПРОС / ВАРИАНТЫ с компромиссами / РЕКОМЕНДАЦИЯ / ЖДУ + таймаут`)
   with post-processing sanitization of AI pleasantries (`LP-10..15`).

## Rationale

Achieves `< 120` tokens per canonical contract exchange (`M-07`) while giving the human operator
actionable, non-blocking decision cards in Telegram, Web, and CLI.

## Alternatives Considered

- Natural-language English or Russian chat between agents: rejected due to token waste and drift.
- Chinese internal prompts: rejected due to BPE boundary friction against ASCII Python/TS code (`§14.1`).

## Consequences

- Repeated style violations by an agent trigger `STYLE_VIOLATION_REPEAT` and automatic reasoning
  budget throttling (`ZST-06`).
