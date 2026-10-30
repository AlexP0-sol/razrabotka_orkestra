"""Deadlock, starvation, and no-progress watchdog detectors for the swarm (§10, SW-18..SW-21)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from orchestra.config.constants import MAX_RETRIES, NO_PROGRESS_ALERT_S
from orchestra.state.mental_map import MentalMap

TERMINAL_NODE_STATUSES: frozenset[str] = frozenset({"DONE", "FAILED", "CANCELLED"})


@dataclass(frozen=True, slots=True)
class DeadlockReport:
    """Structured diagnosis produced by `DeadlockDetector` (`SW-18..SW-21`)."""

    deadlocked: bool
    stalled: bool
    exhausted_retry_nodes: tuple[str, ...]
    blocked_waiting_nodes: tuple[str, ...]
    unresolved_pending_nodes: tuple[str, ...]
    seconds_since_last_progress: float
    reason: str


class DeadlockDetector:
    """Inspect `MentalMap` progress and flag retry exhaustion, stalls, or structural deadlocks."""

    def __init__(
        self,
        *,
        max_retries: int = MAX_RETRIES,
        no_progress_alert_s: float = float(NO_PROGRESS_ALERT_S),
    ) -> None:
        """Initialize detector thresholds from `constants.py` defaults."""
        self._max_retries = max_retries
        self._no_progress_alert_s = no_progress_alert_s
        self._last_progress_at = datetime.now(UTC)
        self._last_done_count = 0

    def record_progress(self, now: datetime | None = None) -> None:
        """Reset the no-progress timer when a node completes or advances."""
        self._last_progress_at = now or datetime.now(UTC)

    def inspect(self, mental_map: MentalMap, *, now: datetime | None = None) -> DeadlockReport:
        """Evaluate whether the build graph is making progress, stalled, or deadlocked."""
        ts = now or datetime.now(UTC)
        done_count = sum(1 for n in mental_map.nodes.values() if n.status == "DONE")
        if done_count > self._last_done_count:
            self._last_done_count = done_count
            self._last_progress_at = ts

        elapsed_s = max(0.0, (ts - self._last_progress_at).total_seconds())
        unfinished = [
            n for n in mental_map.nodes.values() if n.status not in TERMINAL_NODE_STATUSES
        ]
        if not unfinished:
            return DeadlockReport(
                deadlocked=False,
                stalled=False,
                exhausted_retry_nodes=(),
                blocked_waiting_nodes=(),
                unresolved_pending_nodes=(),
                seconds_since_last_progress=elapsed_s,
                reason="ALL_TERMINAL",
            )

        active = [
            n.id
            for n in unfinished
            if n.status in {"READY", "LEASED", "IN_PROGRESS", "IN_REVIEW", "VERIFYING"}
        ]
        exhausted = tuple(sorted(n.id for n in unfinished if n.retry_count >= self._max_retries))
        blocked = tuple(
            sorted(n.id for n in unfinished if n.status in {"BLOCKED_HUMAN", "BLOCKED_DISCOVERY"})
        )
        pending = tuple(sorted(n.id for n in unfinished if n.status in {"PENDING_DEPS", "DRAFT"}))

        # Structural deadlock:unfinished nodes remain, zero active/runnable, and none waiting human
        deadlocked = len(active) == 0 and len(blocked) == 0 and len(pending) > 0
        stalled = elapsed_s >= self._no_progress_alert_s and len(active) > 0

        reason = "OK"
        if deadlocked:
            reason = "UNRUNNABLE_PENDING_SUBGRAPH"
        elif exhausted:
            reason = "RETRY_BUDGET_EXHAUSTED"
        elif stalled:
            reason = "NO_PROGRESS_TIMEOUT"
        elif blocked and not active:
            reason = "WAITING_ON_HUMAN_ESCALATION"

        return DeadlockReport(
            deadlocked=deadlocked,
            stalled=stalled,
            exhausted_retry_nodes=exhausted,
            blocked_waiting_nodes=blocked,
            unresolved_pending_nodes=pending,
            seconds_since_last_progress=elapsed_s,
            reason=reason,
        )
