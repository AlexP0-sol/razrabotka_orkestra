"""Async EventBus for inter-agent RPC mailboxes, request-reply correlation, and UI telemetry."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from orchestra.comms.rpc_protocol import RpcEnvelope
from orchestra.config.constants import WS_EVENT_BUFFER

if TYPE_CHECKING:
    from pathlib import Path


class BusTopicEvent(BaseModel):
    """Telemetry/UI broadcast event buffered in ring buffer for WebSocket replay (WS-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    event_id: str = Field(default_factory=lambda: f"bev_{uuid.uuid4().hex[:10]}")
    topic: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)


TopicSubscriber = Callable[[BusTopicEvent], Awaitable[None] | None]


class EventBus:
    """Routes validated `RpcEnvelope` messages between agents and streams `BusTopicEvent`s."""

    def __init__(
        self,
        *,
        rpc_log_path: Path | None = None,
        ring_buffer_size: int = WS_EVENT_BUFFER,
    ) -> None:
        """Initialize EventBus with optional JSONL audit log and WS ring buffer."""
        self._rpc_log_path = rpc_log_path
        if self._rpc_log_path is not None:
            self._rpc_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._mailboxes: dict[str, asyncio.Queue[RpcEnvelope]] = {}
        self._pending_replies: dict[str, asyncio.Future[RpcEnvelope]] = {}
        self._subscribers: list[TopicSubscriber] = []
        self._ring: deque[BusTopicEvent] = deque(maxlen=max(10, ring_buffer_size))
        self._seq = 0

    def register_agent(self, agent_id: str) -> asyncio.Queue[RpcEnvelope]:
        """Ensure an inbox queue exists for `agent_id` and return it."""
        box = self._mailboxes.get(agent_id)
        if box is None:
            box = asyncio.Queue()
            self._mailboxes[agent_id] = box
        return box

    def subscribe_topics(self, callback: TopicSubscriber) -> Callable[[], None]:
        """Subscribe to all `BusTopicEvent` emissions; returns an unsubscribe callable."""
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def recent_events(self, *, since_seq: int = 0) -> list[BusTopicEvent]:
        """Return buffered `BusTopicEvent`s with `seq > since_seq` for WebSocket catch-up."""
        return [ev for ev in self._ring if ev.seq > since_seq]

    async def publish_topic(self, topic: str, data: dict[str, Any]) -> BusTopicEvent:
        """Publish a UI/telemetry event into the ring buffer and notify subscribers."""
        self._seq += 1
        event = BusTopicEvent(seq=self._seq, topic=topic, data=data)
        self._ring.append(event)
        for sub in list(self._subscribers):
            res = sub(event)
            if res is not None:
                await res
        return event

    async def send_rpc(self, envelope: RpcEnvelope) -> None:
        """Validate TTL, log to JSONL, resolve correlated waiter or deliver to recipient inbox."""
        envelope.assert_not_expired()
        self._append_rpc_log(envelope)

        resolved_waiter = False
        if envelope.correlation_id and envelope.correlation_id in self._pending_replies:
            fut = self._pending_replies.pop(envelope.correlation_id)
            if not fut.done():
                fut.set_result(envelope)
                resolved_waiter = True

        if not resolved_waiter:
            if envelope.recipient_id == "*":
                for aid, box in self._mailboxes.items():
                    if aid != envelope.sender_id:
                        await box.put(envelope)
            else:
                box = self.register_agent(envelope.recipient_id)
                await box.put(envelope)

        await self.publish_topic(
            "rpc.message",
            {
                "msg_id": envelope.msg_id,
                "sender_id": envelope.sender_id,
                "recipient_id": envelope.recipient_id,
                "msg_type": envelope.msg_type,
                "node_id": envelope.node_id,
            },
        )

    async def receive_rpc(self, agent_id: str, *, timeout_s: float = 5.0) -> RpcEnvelope:
        """Receive next non-expired RPC envelope from `agent_id`'s inbox."""
        box = self.register_agent(agent_id)
        while True:
            env = await asyncio.wait_for(box.get(), timeout=timeout_s)
            env.assert_not_expired()
            return env

    async def request_reply(
        self,
        request: RpcEnvelope,
        *,
        timeout_s: float | None = None,
    ) -> RpcEnvelope:
        """Send `request` and await correlated reply matching `request.msg_id` (RPC-06)."""
        wait_s = timeout_s if timeout_s is not None else float(request.ttl_s)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[RpcEnvelope] = loop.create_future()
        self._pending_replies[request.msg_id] = fut
        try:
            await self.send_rpc(request)
            return await asyncio.wait_for(fut, timeout=wait_s)
        finally:
            self._pending_replies.pop(request.msg_id, None)

    def _append_rpc_log(self, envelope: RpcEnvelope) -> None:
        if self._rpc_log_path is None:
            return
        with self._rpc_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(envelope.model_dump_json() + "\n")
