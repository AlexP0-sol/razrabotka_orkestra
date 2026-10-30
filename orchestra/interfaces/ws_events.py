"""WebSocket hub with snapshot bootstrap, ring-buffer catch-up, and live event broadcast (§27.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fastapi import WebSocket

    from orchestra.comms.event_bus import BusTopicEvent, EventBus
    from orchestra.inference.key_pool import KeyPoolManager
    from orchestra.skills.registry import SkillRegistry
    from orchestra.state.state_store import StateStore


class WSFrame(BaseModel):
    """Canonical JSON frame transmitted over `WS /ws` (§27.2, WS-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    seq: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class WebSocketHub:
    """Manages active browser WebSocket clients and bridges `EventBus` + `StateStore` updates."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        event_bus: EventBus,
        key_pool: KeyPoolManager | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        """Initialize WebSocketHub and subscribe to bus/state streams."""
        self._store = state_store
        self._bus = event_bus
        self._key_pool = key_pool
        self._skills = skill_registry
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

        self._bus.subscribe_topics(self._on_bus_event)
        self._store.subscribe(self._on_state_mutated)

    def build_init_frame(self, *, since_seq: int = 0) -> WSFrame:
        """Build `snapshot.init` frame including missed ring-buffer events (WS-03)."""
        snap = self._store.snapshot()
        keys = (
            [k.model_dump(mode="json") for k in self._key_pool.snapshot_public()]
            if self._key_pool is not None
            else []
        )
        skills_count = len(self._skills.manifest.skills) if self._skills is not None else 0
        missed = [ev.model_dump(mode="json") for ev in self._bus.recent_events(since_seq=since_seq)]
        latest_seq = missed[-1]["seq"] if missed else since_seq
        return WSFrame(
            type="snapshot.init",
            seq=int(latest_seq),
            payload={
                "mental_map": snap.model_dump(mode="json"),
                "mermaid": snap.export_mermaid(),
                "key_pool": keys,
                "skills_count": skills_count,
                "missed_events": missed,
            },
        )

    async def connect(self, websocket: WebSocket, *, since_seq: int = 0) -> None:
        """Accept `websocket`, send `snapshot.init`, and register for live broadcasts."""
        await websocket.accept()
        init_frame = self.build_init_frame(since_seq=since_seq)
        await websocket.send_text(init_frame.model_dump_json())
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove `websocket` from active broadcast set."""
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, frame: WSFrame) -> None:
        """Broadcast `frame` to all connected clients, pruning dead sockets."""
        text = frame.model_dump_json()
        async with self._lock:
            targets = list(self._clients)
        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        if stale:
            async with self._lock:
                for dead in stale:
                    self._clients.discard(dead)

    async def _on_bus_event(self, event: BusTopicEvent) -> None:
        await self.broadcast(
            WSFrame(
                type=event.topic,
                seq=event.seq,
                timestamp=event.timestamp,
                payload=event.data,
            )
        )

    async def _on_state_mutated(self, new_map: Any, state_ev: Any) -> None:
        await self.broadcast(
            WSFrame(
                type="map.updated",
                seq=state_ev.seq,
                payload={
                    "mental_map": new_map.model_dump(mode="json"),
                    "mermaid": new_map.export_mermaid(),
                    "mutation_type": state_ev.mutation_type,
                },
            )
        )
