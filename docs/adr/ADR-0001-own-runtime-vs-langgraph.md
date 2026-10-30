# ADR-0001: Custom Pure-Asyncio Orchestration Runtime vs External Agent Frameworks

- **ID:** ADR-0001
- **Date:** 2026-10-30
- **Status:** Accepted (`C-15`, `BAN-15`)

## Context

ORCHESTRA requires deterministic topological DAG scheduling with critical-path prioritization,
per-node lease heartbeats, single-writer optimistic concurrency, dual-stream LLM separation
(`reasoning_content` vs `content`), non-blocking per-node human escalation (`asyncio.Event`),
and strict ASCII Telegraphic JSON-RPC (`INTER_AGENT_RPC_V1`).

## Decision

Implement a lightweight custom orchestration runtime directly on Python 3.11+ `asyncio`
(`TaskGroup`, `Condition`, `Semaphore`, `Queue`) and Pydantic v2 models. Explicitly forbid
`langgraph`, `langchain`, `autogen`, `crewai`, `llama-index`, and `openai-agents` as runtime
dependencies.

## Rationale

1. External agent frameworks impose opaque graph/state abstractions, prompt wrappers, and message
   histories that conflict with our 20/30/50 context budget (`§25`) and Zero Small-Talk RPC (`§19`).
2. Crash recovery (`SIGKILL` / `TerminateProcess`) requires full control over event-sourced replay
   (`events.jsonl`) and atomic snapshot replacement (`mental_map.json`).
3. Zero framework bloat keeps cold-start sub-second, simplifies `mypy --strict` compliance, and
   eliminates breaking upstream API churn.

## Alternatives Considered

- **LangGraph:** Strong state-graph primitives, but couples execution checkpoints to LangChain
  message types and complicates fine-grained key-lease failover mid-node.
- **AutoGen / CrewAI:** Designed around conversational multi-agent chat loops, directly violating
  `C-12` (no horizontal debates) and `ZST-01` (no natural-language agent chat).

## Consequences

- Full ownership of scheduler, FSM transitions, and event outbox (`~1,200` lines of clean typed code).
- Requires dedicated unit, property-based (`hypothesis`), and chaos tests (`T-02`, `T-07`, `T-09`).
