"""Stateful single-agent execution runtime with sliding history, lease heartbeats, and budgets."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ConfigDict

from orchestra.agents.charter import AgentCharter
from orchestra.config.constants import AGENT_HISTORY_WINDOW, LEASE_HEARTBEAT_S, LEASE_TTL_S
from orchestra.errors import BudgetExceeded
from orchestra.providers.base import ChatMessage, ToolDefinition
from orchestra.talents.talents_store import TalentProfile

if TYPE_CHECKING:
    from orchestra.inference.llm_client import DualStreamLLMRuntime, LLMResult
    from orchestra.state.mental_map import NodeLease, SubmapProjection
    from orchestra.state.state_store import StateStore

ModelT = TypeVar("ModelT", bound=BaseModel)


class HistoryDigestEntry(BaseModel):
    """Compressed memory turn retained when rolling past AGENT_HISTORY_WINDOW (§25)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int
    node_id: str | None
    summary: str


class AgentExecutionResult(BaseModel):
    """Outcome of an agent executing a node turn before Independent Judge verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    role_id: str
    node_id: str
    content_text: str
    tokens_used: int
    proposed_payload: dict[str, Any]


class AgentRuntime:
    """Executes bounded LLM turns for one chartered specialist agent (§13.6, SW-10..15)."""

    def __init__(
        self,
        *,
        charter: AgentCharter,
        talent: TalentProfile,
        llm_runtime: DualStreamLLMRuntime,
        state_store: StateStore,
        history_window: int = AGENT_HISTORY_WINDOW,
        heartbeat_interval_s: float = LEASE_HEARTBEAT_S,
        lease_ttl_s: int = LEASE_TTL_S,
    ) -> None:
        """Initialize AgentRuntime bound to `charter` and `talent`."""
        self.charter = charter
        self.talent = talent
        self._llm = llm_runtime
        self._store = state_store
        self._window = max(2, history_window)
        self._heartbeat_s = heartbeat_interval_s
        self._lease_ttl_s = lease_ttl_s
        self._recent_messages: deque[ChatMessage] = deque()
        self._digests: list[HistoryDigestEntry] = []
        self._turn_counter = 0

    @property
    def digests(self) -> tuple[HistoryDigestEntry, ...]:
        """Return compressed digest entries of older turns."""
        return tuple(self._digests)

    def build_system_prompt(self, submap: SubmapProjection | None = None) -> str:
        """Build structured role system prompt (20% persona, 30% submap, 50% task/skills)."""
        heuristics = "; ".join(self.talent.proven_heuristics[:8]) or "NONE"
        pitfalls = "; ".join(self.talent.known_pitfalls[:8]) or "NONE"
        parts = [
            f"ROLE: {self.charter.display_name} ({self.charter.agent_id})",
            f"MISSION: {self.charter.mission}",
            f"MAX_CHUNK_LINES: {self.charter.max_chunk_lines}",
            f"PROVEN_HEURISTICS: {heuristics}",
            f"KNOWN_PITFALLS: {pitfalls}",
            (
                "THINKING_PROTOCOL: 1) Boundary & contract check 2) Diary & antipattern check "
                "3) Security/race check 4) Skeleton design 5) Self-critique."
            ),
        ]
        if submap is not None:
            contracts_txt = ", ".join(f"{c.id}@v{c.version}" for c in submap.contracts) or "NONE"
            deps_txt = ", ".join(n.id for n in submap.upstream_nodes) or "NONE"
            parts.append(
                f"SUBMAP_NODE: {submap.focus_node.id} ({submap.focus_node.title}) | "
                f"DEPS: {deps_txt} | CONTRACTS: {contracts_txt}"
            )
            if submap.focus_node.rework_defects:
                parts.append("REWORK_DEFECTS: " + " | ".join(submap.focus_node.rework_defects))
        if self._digests:
            recent_digests = " ; ".join(d.summary for d in self._digests[-5:])
            parts.append(f"PRIOR_TURN_DIGESTS: {recent_digests}")
        return "\n".join(parts)

    async def run_turn(
        self,
        user_instruction: str,
        *,
        node_id: str | None = None,
        submap: SubmapProjection | None = None,
        tools: tuple[ToolDefinition, ...] = (),
        response_schema: type[ModelT] | None = None,
        trivial: bool = False,
    ) -> LLMResult[ModelT]:
        """Run one reasoned LLM turn, updating node token accounting and rolling history."""
        if node_id is not None:
            snap = self._store.snapshot()
            node = snap.nodes.get(node_id)
            if node is not None and node.tokens_used >= node.token_budget:
                raise BudgetExceeded(
                    f"Node '{node_id}' exhausted token budget "
                    f"({node.tokens_used}/{node.token_budget})"
                )

        sys_msg = ChatMessage(role="system", content=self.build_system_prompt(submap))
        user_msg = ChatMessage(role="user", content=user_instruction)
        messages = (sys_msg, *self._recent_messages, user_msg)

        result = await self._llm.complete(
            messages,
            role=self.charter.role_id,
            agent_id=self.charter.agent_id,
            node_id=node_id,
            tools=tools,
            response_schema=response_schema,
            trivial=trivial,
        )

        self._record_turn(user_msg, result.content_text, node_id=node_id)

        if node_id is not None and result.total_tokens > 0:
            snap = self._store.snapshot()
            if node_id in snap.nodes:
                cur_status = snap.nodes[node_id].status
                await self._store.mutate(
                    actor=self.charter.agent_id,
                    mutation_type="transition_node",
                    payload={
                        "node_id": node_id,
                        "status": cur_status,
                        "tokens_used_delta": result.total_tokens,
                    },
                )
        return result

    async def execute_leased_node(
        self,
        *,
        node_id: str,
        lease: NodeLease,
        instruction: str,
        response_schema: type[ModelT] | None = None,
    ) -> AgentExecutionResult:
        """Transition leased node to IN_PROGRESS, maintain heartbeats, and submit to VERIFYING."""
        await self._store.mutate(
            actor=self.charter.agent_id,
            mutation_type="transition_node",
            payload={"node_id": node_id, "status": "IN_PROGRESS"},
        )
        hb_task = asyncio.create_task(self._heartbeat_loop(node_id=node_id, lease=lease))
        try:
            submap = self._store.snapshot().project_submap(node_id)
            res = await self.run_turn(
                instruction,
                node_id=node_id,
                submap=submap,
                response_schema=response_schema,
            )
            payload_dict = (
                res.structured.model_dump(mode="json") if res.structured is not None else {}
            )
            # BAN-07: Agent transitions node to VERIFYING, NEVER directly to DONE!
            await self._store.mutate(
                actor=self.charter.agent_id,
                mutation_type="transition_node",
                payload={"node_id": node_id, "status": "VERIFYING"},
            )
            return AgentExecutionResult(
                agent_id=self.charter.agent_id,
                role_id=self.charter.role_id,
                node_id=node_id,
                content_text=res.content_text,
                tokens_used=res.total_tokens,
                proposed_payload=payload_dict,
            )
        finally:
            hb_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task

    async def _heartbeat_loop(self, *, node_id: str, lease: NodeLease) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            with suppress(Exception):
                await self._store.mutate(
                    actor=self.charter.agent_id,
                    mutation_type="heartbeat_lease",
                    payload={
                        "node_id": node_id,
                        "lease_id": lease.lease_id,
                        "ttl_s": self._lease_ttl_s,
                    },
                )

    def _record_turn(
        self, user_msg: ChatMessage, assistant_text: str, *, node_id: str | None
    ) -> None:
        self._turn_counter += 1
        self._recent_messages.append(user_msg)
        self._recent_messages.append(ChatMessage(role="assistant", content=assistant_text))
        while len(self._recent_messages) > self._window * 2:
            old_u = self._recent_messages.popleft()
            old_a = self._recent_messages.popleft()
            snippet = old_a.content.replace("\n", " ")[:120]
            self._digests.append(
                HistoryDigestEntry(
                    turn_index=len(self._digests) + 1,
                    node_id=node_id,
                    summary=f"Q:{old_u.content[:40]} -> A:{snippet}",
                )
            )
