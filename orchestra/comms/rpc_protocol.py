"""Strict INTER_AGENT_RPC_V1 envelope and typed payload schemas (§13.8, §19.1, INV-08, BAN-08)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from orchestra.comms.telegraphic import TelegraphicValidator
from orchestra.config.constants import RPC_DEFAULT_TTL_S
from orchestra.errors import RpcExpiredError, RpcSchemaViolation

RpcMessageType = Literal[
    "TASK_ASSIGN",
    "TASK_SUBMIT",
    "VERIFY_VERDICT",
    "CONTRACT_QUERY",
    "CONTRACT_REPLY",
    "DEFECT_REPORT",
    "ESCALATION_REQ",
    "SKILL_PROPOSE",
    "HEARTBEAT",
    "BROADCAST_EVENT",
]

_VALIDATOR = TelegraphicValidator()


class TaskAssignPayload(BaseModel):
    """Payload for `TASK_ASSIGN` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    lease_id: str
    summary: str


class TaskSubmitPayload(BaseModel):
    """Payload for `TASK_SUBMIT` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    artifact_paths: tuple[str, ...] = ()
    summary: str


class VerifyVerdictPayload(BaseModel):
    """Payload for `VERIFY_VERDICT` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    verdict: Literal["PASS", "REWORK", "BLOCKED"]
    defects: tuple[str, ...] = ()
    summary: str = ""


class ContractQueryPayload(BaseModel):
    """Payload for `CONTRACT_QUERY` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    question_code: str


class ContractReplyPayload(BaseModel):
    """Payload for `CONTRACT_REPLY` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    version: int
    sha256: str
    summary: str


class DefectReportPayload(BaseModel):
    """Payload for `DEFECT_REPORT` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    defects: tuple[str, ...]


class EscalationReqPayload(BaseModel):
    """Payload for `ESCALATION_REQ` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str | None = None
    reason_code: str
    summary: str


class SkillProposePayload(BaseModel):
    """Payload for `SKILL_PROPOSE` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    version: str
    summary: str


class HeartbeatPayload(BaseModel):
    """Payload for `HEARTBEAT` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    lease_id: str


class BroadcastEventPayload(BaseModel):
    """Payload for `BROADCAST_EVENT` message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_code: str
    summary: str


PAYLOAD_SCHEMAS: dict[RpcMessageType, type[BaseModel]] = {
    "TASK_ASSIGN": TaskAssignPayload,
    "TASK_SUBMIT": TaskSubmitPayload,
    "VERIFY_VERDICT": VerifyVerdictPayload,
    "CONTRACT_QUERY": ContractQueryPayload,
    "CONTRACT_REPLY": ContractReplyPayload,
    "DEFECT_REPORT": DefectReportPayload,
    "ESCALATION_REQ": EscalationReqPayload,
    "SKILL_PROPOSE": SkillProposePayload,
    "HEARTBEAT": HeartbeatPayload,
    "BROADCAST_EVENT": BroadcastEventPayload,
}


class RpcEnvelope(BaseModel):
    """Validated inter-agent message envelope rejecting prose and invalid payloads (INV-08)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["INTER_AGENT_RPC_V1"] = "INTER_AGENT_RPC_V1"
    msg_id: str = Field(default_factory=lambda: f"rpc_{uuid.uuid4().hex[:12]}")
    correlation_id: str | None = None
    sender_id: str
    recipient_id: str
    msg_type: RpcMessageType
    node_id: str | None = None
    map_version: int = Field(default=1, ge=1)
    ttl_s: int = Field(default=RPC_DEFAULT_TTL_S, ge=1, le=3_600)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    note: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_envelope_and_style(self) -> RpcEnvelope:
        if not self.sender_id.strip() or not self.recipient_id.strip():
            raise RpcSchemaViolation("sender_id and recipient_id must be non-empty")
        if not self.sender_id.isascii() or not self.recipient_id.isascii():
            raise RpcSchemaViolation("sender_id and recipient_id must be ASCII identifiers")

        _VALIDATOR.validate_note(self.note)

        schema_cls = PAYLOAD_SCHEMAS[self.msg_type]
        try:
            typed = schema_cls.model_validate(self.payload)
        except ValidationError as exc:
            raise RpcSchemaViolation(f"Invalid payload for {self.msg_type}: {exc}") from exc

        summary = getattr(typed, "summary", None)
        if isinstance(summary, str) and summary:
            _VALIDATOR.validate_summary(summary)
        defects = getattr(typed, "defects", None)
        if isinstance(defects, tuple) and defects:
            _VALIDATOR.validate_defects(defects)
        q_code = getattr(typed, "question_code", None)
        if isinstance(q_code, str) and q_code:
            _VALIDATOR.validate_note(q_code)
        r_code = getattr(typed, "reason_code", None)
        if isinstance(r_code, str) and r_code:
            _VALIDATOR.validate_note(r_code)
        return self

    def assert_not_expired(self, *, now: datetime | None = None) -> None:
        """Raise `RpcExpiredError` if envelope TTL has elapsed (RPC-05)."""
        ref = now or datetime.now(UTC)
        age = (ref - self.created_at).total_seconds()
        if age > self.ttl_s:
            raise RpcExpiredError(
                f"RPC message '{self.msg_id}' expired ({age:.1f}s > ttl {self.ttl_s}s)"
            )


def parse_rpc_envelope(raw: dict[str, Any] | str) -> RpcEnvelope:
    """Validate raw dict or JSON string as an `RpcEnvelope`, raising `RpcSchemaViolation`."""
    try:
        if isinstance(raw, str):
            return RpcEnvelope.model_validate_json(raw)
        return RpcEnvelope.model_validate(raw)
    except (RpcSchemaViolation, ValidationError) as exc:
        if isinstance(exc, RpcSchemaViolation):
            raise
        raise RpcSchemaViolation(f"RPC schema violation: {exc}") from exc
