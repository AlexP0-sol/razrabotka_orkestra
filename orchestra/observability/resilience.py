"""Circuit breaker, atomic checkpointing, crash recovery, and graceful shutdown (§30, RS-10..16)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestra.errors import ProviderUnavailable

if TYPE_CHECKING:
    from orchestra.observability.metrics import MetricsCollector
    from orchestra.state.state_store import StateStore
    from orchestra.swarm.orchestrator import SwarmOrchestrator

CircuitState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class CircuitBreaker:
    """Three-state circuit breaker guarding flaky external endpoints (RS-10)."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
    ) -> None:
        """Initialize CircuitBreaker."""
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout_s = max(0.1, recovery_timeout_s)
        self._failures = 0
        self._opened_at: float | None = None
        self._state: CircuitState = "CLOSED"

    @property
    def state(self) -> CircuitState:
        """Return current circuit state, transitioning `OPEN -> HALF_OPEN` after cooldown."""
        if (
            self._state == "OPEN"
            and self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self._recovery_timeout_s
        ):
            self._state = "HALF_OPEN"
        return self._state

    def ensure_allow_request(self) -> None:
        """Raise `ProviderUnavailable` when circuit is `OPEN`."""
        if self.state == "OPEN":
            raise ProviderUnavailable("Circuit breaker OPEN — внешний сервис временно изолирован.")

    def record_success(self) -> None:
        """Reset failure count and close circuit."""
        self._failures = 0
        self._opened_at = None
        self._state = "CLOSED"

    def record_failure(self) -> None:
        """Increment failure streak and trip circuit to `OPEN` on threshold."""
        self._failures += 1
        if self._failures >= self._failure_threshold or self._state == "HALF_OPEN":
            self._state = "OPEN"
            self._opened_at = time.monotonic()


class RuntimeCheckpoint(BaseModel):
    """Atomic disk checkpoint enabling crash-safe resumption without losing DONE nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_log_seq: int
    mental_map: dict[str, Any]
    metrics: dict[str, Any] = Field(default_factory=dict)


def _write_checkpoint_sync(checkpoint_path: Path, payload: str) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(checkpoint_path)


class CheckpointManager:
    """Persists and restores runtime checkpoints, resetting interrupted in-flight leases."""

    def __init__(self, project_dir: Path) -> None:
        """Initialize CheckpointManager rooted at `project_dir`."""
        self._checkpoint_path = project_dir / "checkpoint.json"

    @property
    def checkpoint_path(self) -> Path:
        """Return path to `checkpoint.json`."""
        return self._checkpoint_path

    async def save_checkpoint(
        self,
        *,
        state_store: StateStore,
        metrics: MetricsCollector | None = None,
    ) -> RuntimeCheckpoint:
        """Atomically persist current `MentalMap`, event sequence, and telemetry."""
        snap = state_store.snapshot()
        ckpt = RuntimeCheckpoint(
            project_id=snap.project_id,
            event_log_seq=snap.version,
            mental_map=snap.model_dump(mode="json"),
            metrics=metrics.snapshot().model_dump(mode="json") if metrics is not None else {},
        )
        await asyncio.to_thread(
            _write_checkpoint_sync,
            self._checkpoint_path,
            json.dumps(ckpt.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        )
        return ckpt

    async def recover_after_crash(self, state_store: StateStore) -> list[str]:
        """Reclaim interrupted `LEASED`/`IN_PROGRESS`/`VERIFYING` nodes back to `READY` (DoD-31)."""
        snap = state_store.snapshot()
        reclaimed_nodes: list[dict[str, Any]] = []
        reclaimed_ids: list[str] = []
        for nid, node in snap.nodes.items():
            if node.status in ("LEASED", "IN_PROGRESS", "VERIFYING"):
                updated = node.model_copy(
                    update={
                        "status": "READY",
                        "assigned_agent": None,
                        "lease": None,
                    }
                )
                reclaimed_nodes.append(updated.model_dump(mode="json"))
                reclaimed_ids.append(nid)

        if reclaimed_nodes:
            await state_store.mutate(
                actor="crash_recovery",
                mutation_type="upsert_nodes",
                payload={"nodes": reclaimed_nodes},
            )
        return sorted(reclaimed_ids)


class GracefulShutdownCoordinator:
    """Handles SIGINT/SIGTERM to pause scheduling, flush `StateStore`, and write checkpoint."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        checkpoint_manager: CheckpointManager,
        orchestrator: SwarmOrchestrator | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        """Initialize GracefulShutdownCoordinator."""
        self._store = state_store
        self._ckpt = checkpoint_manager
        self._orchestrator = orchestrator
        self._metrics = metrics
        self._shutting_down = False

    @property
    def is_shutting_down(self) -> bool:
        """Return True once graceful shutdown has been initiated."""
        return self._shutting_down

    def install_signal_handlers(self) -> None:
        """Register cross-platform SIGINT/SIGTERM hooks triggering `trigger_shutdown`."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
            except NotImplementedError:
                # Windows ProactorEventLoop fallback
                signal.signal(
                    sig,
                    lambda _s, _f: loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.shutdown())
                    ),
                )

    async def shutdown(self) -> RuntimeCheckpoint:
        """Stop orchestrator dispatch, write atomic checkpoint, and flush `StateStore` (DoD-32)."""
        self._shutting_down = True
        if self._orchestrator is not None:
            self._orchestrator.request_shutdown()
        ckpt = await self._ckpt.save_checkpoint(state_store=self._store, metrics=self._metrics)
        await self._store.stop()
        return ckpt
