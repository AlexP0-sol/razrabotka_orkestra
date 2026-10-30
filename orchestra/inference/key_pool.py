"""Multi-key API pool with concurrency leases, RPM/TPM buckets, and quarantine (KP-01..11)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from orchestra.config.constants import (
    KEY_COOLDOWN_5XX_S,
    KEY_COOLDOWN_429_S,
    KEY_MAX_CONSECUTIVE_FAILURES,
    MAX_CONCURRENT_PER_KEY,
)
from orchestra.errors import InvalidKeyLeaseError, NoKeysAvailable
from orchestra.providers.base import compute_key_fingerprint

if TYPE_CHECKING:
    from pathlib import Path

KeyStatus = Literal["ACTIVE", "COOLING", "QUARANTINED", "DEAD"]
ReleaseOutcome = Literal["ok", "rate_limit", "server_error", "auth_failed"]

QUARANTINE_DURATION_S: float = 600.0
WINDOW_SECONDS: float = 60.0


@dataclass
class KeySlot:
    """Internal mutable state of one API key inside KeyPoolManager (KP-01, KP-02)."""

    key_id: str
    raw_key: SecretStr
    status: KeyStatus = "ACTIVE"
    active_leases: set[str] = field(default_factory=set)
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_errors_429: int = 0
    total_errors_5xx: int = 0
    rpm_limit: int = 40
    tpm_limit: int = 120_000
    last_used_seq: int = 0
    request_timestamps: deque[float] = field(default_factory=deque)
    token_events: deque[tuple[float, int]] = field(default_factory=deque)

    def prune_window(self, now: float) -> None:
        """Evict RPM/TPM sliding-window entries older than 60 seconds."""
        cutoff = now - WINDOW_SECONDS
        while self.request_timestamps and self.request_timestamps[0] <= cutoff:
            self.request_timestamps.popleft()
        while self.token_events and self.token_events[0][0] <= cutoff:
            self.token_events.popleft()

    def used_rpm(self, now: float) -> int:
        """Return request count in the trailing 60-second window."""
        self.prune_window(now)
        return len(self.request_timestamps)

    def used_tpm(self, now: float) -> int:
        """Return token count consumed in the trailing 60-second window."""
        self.prune_window(now)
        return sum(tokens for _, tokens in self.token_events)


class KeyLease(BaseModel):
    """Opaque lease token returned by `KeyPoolManager.acquire` (INV-09)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str
    key_id: str
    agent_id: str
    raw_key: SecretStr
    estimated_tokens: int = 0

    def __repr__(self) -> str:
        """Never include raw API key in repr (INV-09, BAN-04)."""
        return (
            f"KeyLease(lease_id={self.lease_id!r}, "
            f"key_id={self.key_id!r}, agent_id={self.agent_id!r})"
        )


class PublicKeyStats(BaseModel):
    """Sanitized public statistics for Web UI, CLI, and logs (KP-08)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: str
    status: KeyStatus
    active_leases: int
    cooldown_remaining_s: float
    consecutive_failures: int
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_reasoning_tokens: int
    total_errors_429: int
    total_errors_5xx: int
    rpm_limit: int
    tpm_limit: int


AlertCallback = Callable[[str, str], Awaitable[None] | None]


class KeyPoolManager:
    """Async concurrency-safe pool of API keys with least-loaded + round-robin scheduling."""

    def __init__(
        self,
        keys: list[SecretStr | str],
        *,
        state_path: Path | None = None,
        cooldown_429_s: int = KEY_COOLDOWN_429_S,
        cooldown_5xx_s: int = KEY_COOLDOWN_5XX_S,
        max_consecutive_failures: int = KEY_MAX_CONSECUTIVE_FAILURES,
        max_concurrent_per_key: int = MAX_CONCURRENT_PER_KEY,
        exclusive_lease: bool = False,
        default_rpm: int = 40,
        default_tpm: int = 120_000,
        clock: Callable[[], float] = time.monotonic,
        on_alert: AlertCallback | None = None,
    ) -> None:
        """Initialize KeyPoolManager and restore safe historical stats by `key_id`."""
        self._state_path = state_path
        self._cooldown_429_s = cooldown_429_s
        self._cooldown_5xx_s = cooldown_5xx_s
        self._max_consecutive_failures = max_consecutive_failures
        self._max_concurrent = 1 if exclusive_lease else max(1, max_concurrent_per_key)
        self._default_rpm = default_rpm
        self._default_tpm = default_tpm
        self._clock = clock
        self._on_alert = on_alert

        self._cond = asyncio.Condition()
        self._seq = 0
        self._slots: dict[str, KeySlot] = {}
        self._draining_keys: set[str] = set()

        for item in keys:
            secret = item if isinstance(item, SecretStr) else SecretStr(item)
            raw = secret.get_secret_value().strip()
            if not raw:
                continue
            kid = compute_key_fingerprint(raw)
            self._slots[kid] = KeySlot(
                key_id=kid,
                raw_key=SecretStr(raw),
                rpm_limit=default_rpm,
                tpm_limit=default_tpm,
            )

        self._load_persisted_stats()

    @property
    def size(self) -> int:
        """Total number of registered keys in the pool."""
        return len(self._slots)

    async def acquire(
        self,
        *,
        agent_id: str,
        estimated_tokens: int = 256,
        timeout_s: float = 30.0,
    ) -> KeyLease:
        """Acquire a healthy API key lease or block until cooldown/release (KP-03)."""
        deadline = self._clock() + max(0.01, timeout_s)
        est = max(1, estimated_tokens)

        async with self._cond:
            while True:
                now = self._clock()
                self._wake_expired_cooldowns(now)

                if not self._slots or all(s.status == "DEAD" for s in self._slots.values()):
                    await self._emit_alert(
                        "ALL_KEYS_DEAD",
                        "Все API-ключи в пуле отсутствуют или помечены DEAD (401/403).",
                    )
                    raise NoKeysAvailable("No active API keys: pool is empty or all keys are DEAD")

                slot = self._pick_candidate(now, estimated_tokens=est)
                if slot is not None:
                    self._seq += 1
                    slot.last_used_seq = self._seq
                    lease_id = f"lease_{uuid.uuid4().hex[:12]}"
                    slot.active_leases.add(lease_id)
                    slot.request_timestamps.append(now)
                    slot.token_events.append((now, est))
                    return KeyLease(
                        lease_id=lease_id,
                        key_id=slot.key_id,
                        agent_id=agent_id,
                        raw_key=slot.raw_key,
                        estimated_tokens=est,
                    )

                remaining = deadline - now
                if remaining <= 0:
                    raise NoKeysAvailable(
                        f"Timed out after {timeout_s:.2f}s waiting for an available API key"
                    )

                next_wake = self._next_wakeup_delay(now)
                wait_for = min(remaining, next_wake) if next_wake is not None else remaining
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=max(0.005, wait_for))
                except TimeoutError:
                    # Loop around to wake expired COOLING/QUARANTINED keys or check deadline
                    continue

    async def release(
        self,
        lease: KeyLease,
        *,
        outcome: ReleaseOutcome = "ok",
        retry_after_s: int | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        """Release an acquired lease and update key health/accounting (KP-04..06)."""
        alert_to_send: tuple[str, str] | None = None
        async with self._cond:
            slot = self._slots.get(lease.key_id)
            if slot is None or lease.lease_id not in slot.active_leases:
                raise InvalidKeyLeaseError(f"Unknown or already released lease: {lease.lease_id}")

            slot.active_leases.remove(lease.lease_id)
            now = self._clock()
            slot.total_requests += 1
            slot.total_prompt_tokens += max(0, prompt_tokens)
            slot.total_completion_tokens += max(0, completion_tokens)
            slot.total_reasoning_tokens += max(0, reasoning_tokens)

            actual_total = max(0, prompt_tokens + completion_tokens + reasoning_tokens)
            delta_tokens = actual_total - lease.estimated_tokens
            if delta_tokens > 0:
                slot.token_events.append((now, delta_tokens))

            if outcome == "ok":
                slot.consecutive_failures = 0
                if slot.status in ("COOLING", "QUARANTINED"):
                    slot.status = "ACTIVE"
                    slot.cooldown_until = 0.0
            elif outcome == "rate_limit":
                slot.total_errors_429 += 1
                cooldown = retry_after_s if retry_after_s is not None else self._cooldown_429_s
                slot.status = "COOLING"
                slot.cooldown_until = now + max(1, cooldown)
            elif outcome == "server_error":
                slot.total_errors_5xx += 1
                slot.consecutive_failures += 1
                if slot.consecutive_failures >= self._max_consecutive_failures:
                    slot.status = "QUARANTINED"
                    slot.cooldown_until = now + QUARANTINE_DURATION_S
                    alert_to_send = (
                        "KEY_QUARANTINED",
                        f"Ключ {slot.key_id} переведён в QUARANTINED после "
                        f"{slot.consecutive_failures} сбоев подряд.",
                    )
                else:
                    slot.status = "COOLING"
                    slot.cooldown_until = now + self._cooldown_5xx_s
            elif outcome == "auth_failed":
                slot.status = "DEAD"
                slot.cooldown_until = 0.0
                alert_to_send = (
                    "KEY_DEAD",
                    f"Ключ {slot.key_id} отклонён провайдером (401/403) и помечен DEAD.",
                )

            self._persist_stats()
            self._cond.notify_all()

        if alert_to_send is not None:
            await self._emit_alert(*alert_to_send)

    async def add_key(self, raw_key: SecretStr | str) -> str:
        """Dynamically add a new key without restarting the swarm (KP-07)."""
        secret = raw_key if isinstance(raw_key, SecretStr) else SecretStr(raw_key)
        val = secret.get_secret_value().strip()
        if not val:
            raise ValueError("Cannot add an empty API key")
        kid = compute_key_fingerprint(val)
        async with self._cond:
            if kid not in self._slots:
                self._slots[kid] = KeySlot(
                    key_id=kid,
                    raw_key=SecretStr(val),
                    rpm_limit=self._default_rpm,
                    tpm_limit=self._default_tpm,
                )
            else:
                self._slots[kid].status = "ACTIVE"
                self._slots[kid].cooldown_until = 0.0
            self._draining_keys.discard(kid)
            self._persist_stats()
            self._cond.notify_all()
        return kid

    async def remove_key(self, key_id: str, *, timeout_s: float = 30.0) -> bool:
        """Gracefully drain active leases on `key_id` and remove it from pool (KP-07)."""
        deadline = self._clock() + timeout_s
        async with self._cond:
            if key_id not in self._slots:
                return False
            self._draining_keys.add(key_id)
            while key_id in self._slots and self._slots[key_id].active_leases:
                rem = deadline - self._clock()
                if rem <= 0:
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=rem)
                except TimeoutError:
                    break
            self._draining_keys.discard(key_id)
            removed = self._slots.pop(key_id, None) is not None
            self._persist_stats()
            self._cond.notify_all()
            return removed

    async def unquarantine(self, key_id: str) -> bool:
        """Manually restore a QUARANTINED or COOLING key back to ACTIVE (KP-07)."""
        async with self._cond:
            slot = self._slots.get(key_id)
            if slot is None or slot.status == "DEAD":
                return False
            slot.status = "ACTIVE"
            slot.cooldown_until = 0.0
            slot.consecutive_failures = 0
            self._persist_stats()
            self._cond.notify_all()
            return True

    async def probe_all(self, probe_fn: Callable[[str], Awaitable[bool]]) -> dict[str, KeyStatus]:
        """Run startup health probe across all keys in parallel (KP-10)."""
        items = list(self._slots.values())

        async def _check(slot: KeySlot) -> tuple[str, bool]:
            try:
                ok = await probe_fn(slot.raw_key.get_secret_value())
                return slot.key_id, ok
            except Exception:
                return slot.key_id, False

        results = await asyncio.gather(*(_check(s) for s in items))
        async with self._cond:
            for kid, ok in results:
                slot = self._slots.get(kid)
                if slot is not None and not ok:
                    slot.status = "DEAD"
            self._persist_stats()
            self._cond.notify_all()
            return {kid: s.status for kid, s in self._slots.items()}

    def snapshot_public(self) -> list[PublicKeyStats]:
        """Return sanitized statistics for every key without exposing secrets (KP-08, INV-09)."""
        now = self._clock()
        out: list[PublicKeyStats] = []
        for kid in sorted(self._slots):
            s = self._slots[kid]
            rem = max(0.0, s.cooldown_until - now) if s.cooldown_until > now else 0.0
            out.append(
                PublicKeyStats(
                    key_id=s.key_id,
                    status=s.status,
                    active_leases=len(s.active_leases),
                    cooldown_remaining_s=round(rem, 2),
                    consecutive_failures=s.consecutive_failures,
                    total_requests=s.total_requests,
                    total_prompt_tokens=s.total_prompt_tokens,
                    total_completion_tokens=s.total_completion_tokens,
                    total_reasoning_tokens=s.total_reasoning_tokens,
                    total_errors_429=s.total_errors_429,
                    total_errors_5xx=s.total_errors_5xx,
                    rpm_limit=s.rpm_limit,
                    tpm_limit=s.tpm_limit,
                )
            )
        return out

    def _wake_expired_cooldowns(self, now: float) -> None:
        for slot in self._slots.values():
            if slot.status in ("COOLING", "QUARANTINED") and now >= slot.cooldown_until:
                slot.status = "ACTIVE"
                slot.cooldown_until = 0.0

    def _pick_candidate(self, now: float, *, estimated_tokens: int) -> KeySlot | None:
        candidates: list[KeySlot] = []
        for slot in self._slots.values():
            if slot.key_id in self._draining_keys or slot.status != "ACTIVE":
                continue
            if len(slot.active_leases) >= self._max_concurrent:
                continue
            if slot.used_rpm(now) >= slot.rpm_limit:
                continue
            used_tpm = slot.used_tpm(now)
            # Allow a single large request on an idle window, otherwise enforce TPM cap
            if used_tpm > 0 and (used_tpm + estimated_tokens) > slot.tpm_limit:
                continue
            candidates.append(slot)

        if not candidates:
            return None
        candidates.sort(key=lambda s: (len(s.active_leases), s.last_used_seq, s.key_id))
        return candidates[0]

    def _next_wakeup_delay(self, now: float) -> float | None:
        delays: list[float] = []
        for slot in self._slots.values():
            if slot.status in ("COOLING", "QUARANTINED") and slot.cooldown_until > now:
                delays.append(slot.cooldown_until - now)
            if slot.request_timestamps:
                delays.append(max(0.01, (slot.request_timestamps[0] + WINDOW_SECONDS) - now))
            if slot.token_events:
                delays.append(max(0.01, (slot.token_events[0][0] + WINDOW_SECONDS) - now))
        return min(delays) if delays else None

    def _persist_stats(self) -> None:
        """Persist only `key_id` and numeric counters — never raw keys (KP-09)."""
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            kid: {
                "status": "ACTIVE" if s.status != "DEAD" else "DEAD",
                "consecutive_failures": s.consecutive_failures,
                "total_requests": s.total_requests,
                "total_prompt_tokens": s.total_prompt_tokens,
                "total_completion_tokens": s.total_completion_tokens,
                "total_reasoning_tokens": s.total_reasoning_tokens,
                "total_errors_429": s.total_errors_429,
                "total_errors_5xx": s.total_errors_5xx,
            }
            for kid, s in self._slots.items()
        }
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(self._state_path)

    def _load_persisted_stats(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for kid, saved in data.items():
            slot = self._slots.get(kid)
            if slot is None or not isinstance(saved, dict):
                continue
            if saved.get("status") == "DEAD":
                slot.status = "DEAD"
            slot.total_requests = int(saved.get("total_requests", 0))
            slot.total_prompt_tokens = int(saved.get("total_prompt_tokens", 0))
            slot.total_completion_tokens = int(saved.get("total_completion_tokens", 0))
            slot.total_reasoning_tokens = int(saved.get("total_reasoning_tokens", 0))
            slot.total_errors_429 = int(saved.get("total_errors_429", 0))
            slot.total_errors_5xx = int(saved.get("total_errors_5xx", 0))

    async def _emit_alert(self, code: str, message: str) -> None:
        if self._on_alert is None:
            return
        res = self._on_alert(code, message)
        if res is not None:
            await res
