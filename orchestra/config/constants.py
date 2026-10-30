"""Canonical system constants for ORCHESTRA (§34.2, BAN-12).

All thresholds, limits, and defaults are defined once here and exposed via Settings (§13.1).
Business logic must never hardcode magic literals outside this module and settings.py.
"""

from __future__ import annotations

from typing import Final

# Code generation & retry limits
MAX_CHUNK_LINES: Final[int] = 300
RECOMMENDED_CHUNK_LINES: Final[int] = 200
MAX_RETRIES: Final[int] = 2
MAX_VERIFY_ITERATIONS: Final[int] = 2
MAX_CONTINUATIONS: Final[int] = 2
MAX_ATTEMPTS: Final[int] = 4
RETRY_BASE_S: Final[float] = 1.0
RETRY_MAX_DELAY_S: Final[float] = 30.0

# Swarm concurrency & escalation caps
MAX_CONCURRENT_AGENTS: Final[int] = 6
MAX_CONCURRENT_LLM_CALLS: Final[int] = 8
MAX_AGENTS_HARD_CAP: Final[int] = 20
MAX_OPEN_QUESTIONS: Final[int] = 3

# Persistence & node leases
SNAPSHOT_EVERY_EVENTS: Final[int] = 200
SNAPSHOT_BACKUPS: Final[int] = 5
LEASE_TTL_S: Final[int] = 600
LEASE_HEARTBEAT_S: Final[int] = 60
CAPABILITIES_CACHE_TTL_S: Final[int] = 86_400

# Provider probe & sandbox limits
PROBE_TIMEOUT_S: Final[float] = 30.0
SANDBOX_TIMEOUT_S: Final[int] = 120
SANDBOX_MEMORY_LIMIT_MB: Final[int] = 2048
SANDBOX_CPU_LIMIT: Final[float] = 1.0
GRACE_KILL_S: Final[float] = 5.0
OUTPUT_CAPTURE_LIMIT: Final[int] = 65_536
ERROR_TAIL_LINES: Final[int] = 200
MUTATION_CHECK_PROBABILITY: Final[float] = 0.2
THINKING_AUDIT_PROBABILITY: Final[float] = 0.2

# Prompt & context budget shares (§25)
CONTEXT_SAFETY_RATIO: Final[float] = 0.85
PERSONA_SHARE: Final[float] = 0.20
SUBMAP_SHARE: Final[float] = 0.30
SKILLS_SHARE: Final[float] = 0.50
DIARY_INJECT_TOKENS: Final[int] = 800
SKILL_INDEX_INJECT_TOKENS: Final[int] = 600
ANTIPATTERN_INJECT_LIMIT: Final[int] = 40
AGENT_HISTORY_WINDOW: Final[int] = 6

# Inter-agent RPC limits (§13.8, §14.1)
RPC_NOTE_TOKEN_LIMIT: Final[int] = 40
RPC_SUMMARY_TOKEN_LIMIT: Final[int] = 60
RPC_DEFECT_TOKEN_LIMIT: Final[int] = 30
RPC_DEFAULT_TTL_S: Final[int] = 30

# Role reasoning token budgets (§15.3)
REASONING_ARCHITECT: Final[int] = 16_384
REASONING_QA: Final[int] = 16_384
REASONING_DEV: Final[int] = 8_192
REASONING_UI: Final[int] = 4_096
REASONING_TRIVIAL: Final[int] = 2_048

# Key pool & circuit breaker (§13.3, §13.20)
KEY_COOLDOWN_429_S: Final[int] = 60
KEY_COOLDOWN_5XX_S: Final[int] = 15
KEY_MAX_CONSECUTIVE_FAILURES: Final[int] = 5
MAX_CONCURRENT_PER_KEY: Final[int] = 2
CB_FAILURE_THRESHOLD: Final[int] = 10
CB_WINDOW_S: Final[int] = 60
CB_OPEN_S: Final[int] = 120
SHUTDOWN_GRACE_S: Final[int] = 30
NO_PROGRESS_ALERT_S: Final[int] = 900

# Human interfaces (Telegram & WebSocket)
TG_QUESTION_TIMEOUT_S: Final[int] = 3_600
TG_INTERVIEW_TIMEOUT_S: Final[int] = 86_400
TG_EDIT_THROTTLE_S: Final[float] = 3.0
TG_MESSAGE_LIMIT: Final[int] = 4_096
TG_CALLBACK_DATA_LIMIT: Final[int] = 64
WS_MAX_DOM_EVENTS: Final[int] = 2_000
WS_EVENT_BUFFER: Final[int] = 1_000

# Quality & project budgets
DEFAULT_SEED: Final[int] = 1_337
DEFAULT_MAX_OUTPUT_TOKENS: Final[int] = 16_384
DEFAULT_REQUEST_TIMEOUT_S: Final[float] = 180.0
NODE_TOKEN_LIMIT: Final[int] = 200_000
MIN_EDGE_CASES_PER_NODE: Final[int] = 10
COVERAGE_CORE_MIN: Final[int] = 85
COVERAGE_TOTAL_MIN: Final[int] = 75
SKILL_DEPRECATE_SUCCESS_RATE: Final[float] = 0.5
SKILL_DEPRECATE_MIN_CALLS: Final[int] = 20

# Crypto-trading domain constants (Part VIII, §38)
CLOCK_SYNC_INTERVAL_S: Final[int] = 300
CLOCK_SKEW_LIMIT_MS: Final[int] = 1_000
LINK_LOSS_LIMIT_S: Final[int] = 30
RECONCILE_INTERVAL_S: Final[int] = 60
WS_MAX_RECONNECTS: Final[int] = 8
WS_SILENCE_TIMEOUT_S: Final[int] = 30
LIQ_WARNING_PCT: Final[int] = 15
BACKTEST_SLIPPAGE_GRID_BPS: Final[tuple[int, ...]] = (0, 2, 5, 10)
DEFAULT_TRADING_MODE: Final[str] = "PAPER"
ALIGN_TOLERANCE_MS: Final[int] = 250

# Windows / cross-platform constants (§13.21)
ATOMIC_REPLACE_RETRIES: Final[int] = 5
ATOMIC_REPLACE_BASE_DELAY_MS: Final[int] = 20
PATH_LENGTH_WARN: Final[int] = 240
DEFAULT_NVIDIA_BASE_URL: Final[str] = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL_ID: Final[str] = "nvidia/nemotron-3.5-lightning-30b-a3b"
