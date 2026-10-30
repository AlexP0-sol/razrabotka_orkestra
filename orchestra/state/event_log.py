"""Append-only JSONL EventLog, atomic cross-platform snapshots, and crash recovery (MM-10..13)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import (
    ATOMIC_REPLACE_BASE_DELAY_MS,
    ATOMIC_REPLACE_RETRIES,
    SNAPSHOT_BACKUPS,
)
from orchestra.errors import CorruptedStateError
from orchestra.state.mental_map import MentalMap

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def atomic_write_text(
    target: Path,
    content: str,
    *,
    retries: int = ATOMIC_REPLACE_RETRIES,
    base_delay_ms: int = ATOMIC_REPLACE_BASE_DELAY_MS,
) -> None:
    """Atomically write LF-normalized UTF-8 text with Windows PermissionError retries (WIN-01)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)
        handle.flush()
        os.fsync(handle.fileno())

    last_err: OSError | None = None
    for attempt in range(retries):
        try:
            tmp_path.replace(target)
            return
        except PermissionError as exc:
            last_err = exc
            delay_s = (base_delay_ms * (2**attempt)) / 1000.0
            time.sleep(delay_s)

    if tmp_path.exists():
        tmp_path.unlink(missing_ok=True)
    assert last_err is not None
    raise last_err


class StateEvent(BaseModel):
    """Immutable append-only event persisted in `events.jsonl` (INV-02, MM-10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    mutation_type: str
    expected_version: int
    new_version: int
    payload: dict[str, Any]
    event_sha256: str = ""

    def with_hash(self) -> StateEvent:
        """Compute and attach deterministic SHA-256 over the event envelope."""
        raw = self.model_dump(mode="json", exclude={"event_sha256"})
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.model_copy(update={"event_sha256": digest})

    def is_hash_valid(self) -> bool:
        """Verify event integrity digest."""
        return bool(self.event_sha256) and self.with_hash().event_sha256 == self.event_sha256


class EventLog:
    """Manages `events.jsonl` durability, rotating `mental_map.json` snapshots, and recovery."""

    def __init__(
        self,
        project_dir: Path,
        *,
        backups: int = SNAPSHOT_BACKUPS,
        atomic_retries: int = ATOMIC_REPLACE_RETRIES,
    ) -> None:
        """Initialize EventLog storage inside `project_dir`."""
        self._dir = project_dir
        self._backups = max(1, backups)
        self._atomic_retries = atomic_retries
        self._dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self._dir / "events.jsonl"
        self.snapshot_path = self._dir / "mental_map.json"

    def append_event(self, event: StateEvent) -> StateEvent:
        """Append one fsync-flushed JSONL line to `events.jsonl` (INV-02)."""
        hashed = event.with_hash()
        line = hashed.model_dump_json() + "\n"
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return hashed

    def write_snapshot(self, mental_map: MentalMap) -> None:
        """Rotate up to `N` backups and atomically write `mental_map.json` (MM-11)."""
        checked = (
            mental_map
            if mental_map.checksum and mental_map.checksum == mental_map.compute_checksum()
            else mental_map.with_updated_checksum(bump_version=False)
        )
        if self.snapshot_path.is_file():
            self._rotate_backups()
        atomic_write_text(
            self.snapshot_path,
            checked.model_dump_json(indent=2),
            retries=self._atomic_retries,
        )

    def _rotate_backups(self) -> None:
        oldest = self._dir / f"mental_map.bak.{self._backups}.json"
        if oldest.is_file():
            oldest.unlink(missing_ok=True)
        for idx in range(self._backups - 1, 0, -1):
            src = self._dir / f"mental_map.bak.{idx}.json"
            dst = self._dir / f"mental_map.bak.{idx + 1}.json"
            if src.is_file():
                src.replace(dst)
        bak_1 = self._dir / "mental_map.bak.1.json"
        shutil.copy2(self.snapshot_path, bak_1)

    def read_events(self, *, after_version: int = 0) -> list[StateEvent]:
        """Read and verify events with `new_version > after_version` from `events.jsonl`."""
        if not self.events_path.is_file():
            return []
        events: list[StateEvent] = []
        raw_lines = self.events_path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(raw_lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ev = StateEvent.model_validate_json(stripped)
            except Exception as exc:
                # Truncated final line after hard power loss is tolerated during replay
                if idx == len(raw_lines) - 1:
                    break
                raise CorruptedStateError(
                    f"Corrupted JSONL record at line {idx + 1} in {self.events_path}"
                ) from exc
            if not ev.is_hash_valid():
                if idx == len(raw_lines) - 1:
                    break
                raise CorruptedStateError(
                    f"Checksum mismatch at event seq={ev.seq} in {self.events_path}"
                )
            if ev.new_version > after_version:
                events.append(ev)
        return events

    def recover(
        self,
        replay_fn: Callable[[MentalMap, StateEvent], MentalMap],
    ) -> MentalMap | None:
        """Recover latest consistent `MentalMap` from snapshot/backups + event log tail (MM-13)."""
        candidates = [
            self.snapshot_path,
            *(self._dir / f"mental_map.bak.{i}.json" for i in range(1, self._backups + 1)),
        ]
        base_map: MentalMap | None = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                loaded = MentalMap.model_validate_json(candidate.read_text(encoding="utf-8"))
                if loaded.checksum == loaded.compute_checksum():
                    loaded.verify_integrity()
                    base_map = loaded
                    break
            except Exception:
                continue

        if base_map is None:
            if any(c.is_file() for c in candidates):
                raise CorruptedStateError(
                    f"All snapshot files in {self._dir} failed checksum or schema verification"
                )
            return None

        tail_events = self.read_events(after_version=base_map.version)
        cur = base_map
        for ev in tail_events:
            cur = replay_fn(cur, ev)
        cur.verify_integrity()
        return cur
