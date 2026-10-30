"""Typed exception hierarchy for ORCHESTRA (QS-05, OAI-06).

Every domain error inherits from OrchestraError so callers can handle failures deterministically
without resorting to bare Exception or RuntimeError in business logic.
"""

from __future__ import annotations


class OrchestraError(Exception):
    """Base exception for all ORCHESTRA runtime and domain errors."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialize an OrchestraError.

        Args:
            message: Human- or log-readable error description.
            code: Optional stable machine error code.
        """
        super().__init__(message)
        self.code = code or self.__class__.__name__


class ConfigurationError(OrchestraError):
    """Raised when configuration or environment settings fail startup validation (CFG-03)."""


# --- L1 Provider & LLM Errors (OAI-06) ---


class ProviderError(OrchestraError):
    """Base error for LLM provider communication and response failures."""


class ModelNotFoundError(ProviderError):
    """Raised when the configured model does not exist on the provider endpoint (CAP-01)."""


class RateLimited(ProviderError):
    """Raised on HTTP 429 rate-limit responses from an LLM provider."""

    def __init__(
        self, message: str = "Rate limit exceeded", *, retry_after_s: int | None = None
    ) -> None:
        """Initialize RateLimited error with optional Retry-After hint."""
        super().__init__(message, code="RATE_LIMITED")
        self.retry_after_s = retry_after_s


class AuthFailed(ProviderError):
    """Raised on HTTP 401/403 authentication or permission rejection."""


class BadRequest(ProviderError):
    """Raised on HTTP 400 invalid parameter errors that must not be blindly retried."""


class ContextOverflow(ProviderError):
    """Raised when prompt + output budget exceeds the model context window."""


class ProviderUnavailable(ProviderError):
    """Raised on HTTP 5xx transient server-side provider errors."""


class RequestTimeout(ProviderError):
    """Raised when an LLM request or stream exceeds its configured timeout."""


class StreamInterrupted(ProviderError):
    """Raised when a streaming response disconnects mid-generation."""

    def __init__(
        self, message: str, *, partial_content: str = "", partial_reasoning: str = ""
    ) -> None:
        """Initialize StreamInterrupted with partial buffers captured before disconnect."""
        super().__init__(message, code="STREAM_INTERRUPTED")
        self.partial_content = partial_content
        self.partial_reasoning = partial_reasoning


class TruncatedOutput(ProviderError):
    """Raised when generation stops with finish_reason='length'."""

    def __init__(self, message: str, *, partial_content: str = "") -> None:
        """Initialize TruncatedOutput with generated prefix."""
        super().__init__(message, code="TRUNCATED_OUTPUT")
        self.partial_content = partial_content


class ContentFiltered(ProviderError):
    """Raised when provider safety filter blocks generation (finish_reason='content_filter')."""


class StructuredOutputError(ProviderError):
    """Raised when structured JSON extraction and repair both fail (LLM-07)."""

    def __init__(self, message: str, *, raw_output: str = "") -> None:
        """Initialize StructuredOutputError preserving raw model output for audit."""
        super().__init__(message, code="STRUCTURED_OUTPUT_ERROR")
        self.raw_output = raw_output


# --- L1 Key Pool Errors ---


class KeyPoolError(OrchestraError):
    """Base error for API key pool operations."""


class NoKeysAvailable(KeyPoolError):
    """Raised when no usable API keys are available within acquire timeout (KP-03)."""


class InvalidKeyLeaseError(KeyPoolError):
    """Raised when releasing or mutating an unknown or expired key lease."""


# --- L3 State & Mental Map Errors ---


class StateError(OrchestraError):
    """Base error for Mental Map, StateStore, and persistence failures."""


class StaleStateError(StateError):
    """Raised on optimistic concurrency version mismatch (CC-05, MM-12)."""


class CyclicDependencyError(StateError):
    """Raised when a Mental Map mutation introduces a cycle in the DAG (MM-03)."""

    def __init__(self, cycle_path: list[str]) -> None:
        """Initialize CyclicDependencyError with the detected cycle node sequence."""
        self.cycle_path = cycle_path
        joined = " -> ".join(cycle_path)
        super().__init__(f"Cyclic dependency detected in Mental Map: {joined}", code="CYCLIC_DAG")


class IllegalNodeTransition(StateError):
    """Raised when attempting a node status transition outside the FSM table (MM-01)."""


class OrphanReferenceError(StateError):
    """Raised when a node references missing dependencies, contracts, or skills (MM-04)."""


class CorruptedStateError(StateError):
    """Raised when both snapshot checksum and event log recovery fail (MM-13)."""


class FileOwnershipViolation(StateError):
    """Raised when an agent attempts to modify a file owned by another active node (PT-06)."""


class CaseCollisionError(StateError):
    """Raised when creating a file collides case-insensitively with an existing file (WIN-10)."""


# --- L2 Sandbox, Patch & Verification Errors ---


class SandboxError(OrchestraError):
    """Base error for sandbox execution and verifier failures."""


class SandboxTimeoutError(SandboxError):
    """Raised when a sandboxed command exceeds its wall-clock timeout (SB-03)."""


class DisallowedCommand(SandboxError):
    """Raised when a requested command is not permitted by verifier whitelist (SEC-06)."""


class HallucinatedApiError(SandboxError):
    """Raised when imported symbols do not exist in the installed package version (RS-08)."""


class PatchError(OrchestraError):
    """Base error for PatchEngine operations."""


class AmbiguousPatch(PatchError):
    """Raised when SEARCH_REPLACE matches 0 or >1 locations in target file (PT-01)."""


class StaleContextHashError(PatchError):
    """Raised when LINE_RANGE target content SHA-256 does not match expected hash (PT-01)."""


class PatchSyntaxError(PatchError):
    """Raised when patched output fails in-memory AST syntax validation (PT-07)."""


# --- L3 Skills & Diary Errors ---


class SkillError(OrchestraError):
    """Base error for skill registration, mutation, and execution."""


class SkillSecurityError(SkillError):
    """Raised when static security scan rejects a skill prior to registration (SEC-04)."""


class SkillMutationRejected(SkillError):
    """Raised when a skill edit fails Skill Mutation Gate tests or schema compatibility (§21)."""


class SkillExecutionError(SkillError):
    """Raised when an isolated skill subprocess exits non-zero or returns malformed JSON."""


class DiaryGateViolation(OrchestraError):
    """Raised when attempting to record a VERIFIED diary entry without Empirical Proof (TL-07)."""


# --- L4 RPC, Escalation, Budget & Security Errors ---


class RpcError(OrchestraError):
    """Base error for inter-agent RPC validation and routing."""


class RpcSchemaViolation(RpcError):
    """Raised when an incoming RPC message violates INTER_AGENT_RPC_V1 schema (INV-08)."""


class RpcStyleViolation(RpcError):
    """Raised when an RPC payload violates ASCII Telegraphic English rules (LP-06)."""


class RpcExpiredError(RpcError):
    """Raised when an RPC envelope exceeds its TTL window (RPC-05)."""


class ContractFrozenError(RpcError):
    """Raised when mutating a frozen contract without Architect unfreeze procedure (BAN-09)."""


class EscalationError(OrchestraError):
    """Base error for human escalation workflows."""


class QuestionTimeoutError(EscalationError):
    """Raised when a pending human question expires without response."""


class BudgetExceeded(OrchestraError):
    """Raised when node, agent, or project token/wall-clock budgets are exhausted (SW-22)."""


class SecurityViolation(OrchestraError):
    """Raised on path traversal, secret leakage attempt, or blocked prompt injection (§13.19)."""
