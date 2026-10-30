"""Inter-module interface contracts with SHA-256 hashing and freeze governance (MM-05, BAN-09)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.errors import ContractFrozenError

ContractKind = Literal["python_protocol", "json_schema", "rest_endpoint", "event_topic"]


def compute_contract_hash(definition: str) -> str:
    """Compute canonical SHA-256 digest of normalized contract text."""
    normalized = "\n".join(line.rstrip() for line in definition.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ContractSignature(BaseModel):
    """Formal interface contract between producer and consumer nodes in MentalMap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(default=1, ge=1)
    owner_node_id: str
    consumer_node_ids: tuple[str, ...] = ()
    kind: ContractKind = "python_protocol"
    definition: str
    sha256: str = ""
    frozen: bool = False
    frozen_at: datetime | None = None
    unfreeze_reason: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        contract_id: str,
        owner_node_id: str,
        definition: str,
        kind: ContractKind = "python_protocol",
        consumer_node_ids: tuple[str, ...] = (),
        frozen: bool = False,
    ) -> ContractSignature:
        """Create a ContractSignature with automatically computed SHA-256 digest."""
        now = datetime.now(UTC)
        return cls(
            id=contract_id,
            version=1,
            owner_node_id=owner_node_id,
            consumer_node_ids=consumer_node_ids,
            kind=kind,
            definition=definition,
            sha256=compute_contract_hash(definition),
            frozen=frozen,
            frozen_at=now if frozen else None,
            updated_at=now,
        )

    def freeze(self) -> ContractSignature:
        """Return a frozen copy of this contract (Gate G1, §16.2)."""
        if self.frozen:
            return self
        now = datetime.now(UTC)
        return self.model_copy(update={"frozen": True, "frozen_at": now, "updated_at": now})

    def unfreeze(self, *, reason: str) -> ContractSignature:
        """Architect-only unfreeze bumping version and recording justification (BAN-09)."""
        clean_reason = reason.strip()
        if not clean_reason:
            raise ContractFrozenError("Unfreezing a contract requires a non-empty reason")
        now = datetime.now(UTC)
        return self.model_copy(
            update={
                "frozen": False,
                "version": self.version + 1,
                "unfreeze_reason": clean_reason,
                "updated_at": now,
            }
        )

    def update_definition(self, new_definition: str) -> ContractSignature:
        """Update definition when unfrozen; raise ContractFrozenError if frozen (BAN-09)."""
        new_hash = compute_contract_hash(new_definition)
        if self.frozen and new_hash != self.sha256:
            raise ContractFrozenError(
                f"Contract '{self.id}' v{self.version} is FROZEN and cannot be modified without "
                "explicit Architect unfreeze."
            )
        return self.model_copy(
            update={
                "definition": new_definition,
                "sha256": new_hash,
                "updated_at": datetime.now(UTC),
            }
        )
