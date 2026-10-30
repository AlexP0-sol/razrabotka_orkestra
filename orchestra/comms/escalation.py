"""Three-tier escalation manager (L1 Contract -> L2 Architect -> L3 Human) (§23, ESC-01..09)."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from orchestra.config.constants import MAX_OPEN_QUESTIONS, TG_QUESTION_TIMEOUT_S
from orchestra.state.mental_map import OpenQuestion

if TYPE_CHECKING:
    from orchestra.comms.event_bus import EventBus
    from orchestra.state.state_store import StateStore

EscalationLevel = Literal["L1_PEER_CONTRACT", "L2_ARCHITECT", "L3_HUMAN"]
TimeoutPolicy = Literal["pause_node", "apply_recommendation"]


class EscalationRequest(BaseModel):
    """Structured escalation raised by an agent encountering ambiguity or repeated failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    author_agent: str
    node_id: str | None = None
    contract_id: str | None = None
    is_business_decision: bool = False
    question_ru: str
    options_ru: tuple[str, ...] = ("Вариант А (рекомендуется)", "Вариант Б")
    recommendation_idx: int = 0
    impact_ru: str = "Влияет на поведение узла"


class EscalationResolution(BaseModel):
    """Outcome of routing an `EscalationRequest` through L1/L2/L3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: EscalationLevel
    resolved_immediately: bool
    answer: str | None = None
    question_id: str | None = None
    queued_behind_cap: bool = False


class EscalationManager:
    """Enforces local L1/L2 resolution before L3 human prompts and caps open questions at 3."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        event_bus: EventBus | None = None,
        max_open_questions: int = MAX_OPEN_QUESTIONS,
        question_timeout_s: int = TG_QUESTION_TIMEOUT_S,
        timeout_policy: TimeoutPolicy = "pause_node",
    ) -> None:
        """Initialize EscalationManager."""
        self._store = state_store
        self._bus = event_bus
        self._max_open = max(1, max_open_questions)
        self._timeout_s = question_timeout_s
        self._timeout_policy = timeout_policy
        self._backlog: deque[EscalationRequest] = deque()

    @property
    def backlog_size(self) -> int:
        """Return number of human questions waiting behind MAX_OPEN_QUESTIONS cap."""
        return len(self._backlog)

    async def escalate(self, req: EscalationRequest) -> EscalationResolution:
        """Route `req` through L1 (Contract), L2 (Architect), or L3 (Human in Russian)."""
        snap = self._store.snapshot()

        # L1: If question concerns a known contract and is not a business fork, resolve from SSOT
        if req.contract_id and not req.is_business_decision:
            contract = snap.contracts.get(req.contract_id)
            if contract is not None:
                return EscalationResolution(
                    level="L1_PEER_CONTRACT",
                    resolved_immediately=True,
                    answer=f"CONTRACT {contract.id}@v{contract.version}: {contract.definition}",
                )

        # L2: Technical questions with a clear recommended option resolve via Architect policy
        if not req.is_business_decision and req.options_ru:
            rec_idx = min(max(0, req.recommendation_idx), len(req.options_ru) - 1)
            chosen = req.options_ru[rec_idx]
            return EscalationResolution(
                level="L2_ARCHITECT",
                resolved_immediately=True,
                answer=chosen,
            )

        # L3: Business/product decision or unresolvable blocker -> escalate to human in Russian
        if req.node_id and req.node_id in snap.nodes:
            cur_status = snap.nodes[req.node_id].status
            if cur_status not in ("BLOCKED_HUMAN", "DONE", "CANCELLED"):
                await self._store.mutate(
                    actor=req.author_agent,
                    mutation_type="transition_node",
                    payload={"node_id": req.node_id, "status": "BLOCKED_HUMAN"},
                )

        open_count = sum(
            1 for q in self._store.snapshot().open_questions.values() if q.status == "OPEN"
        )
        if open_count >= self._max_open:
            self._backlog.append(req)
            return EscalationResolution(
                level="L3_HUMAN",
                resolved_immediately=False,
                queued_behind_cap=True,
            )

        q_obj = await self._publish_human_question(req)
        return EscalationResolution(
            level="L3_HUMAN",
            resolved_immediately=False,
            question_id=q_obj.id,
        )

    async def answer_question(self, question_id: str, answer: str) -> OpenQuestion:
        """Record human operator's answer, unblock paused node, and promote next queued question."""
        snap = self._store.snapshot()
        existing = snap.open_questions[question_id]
        answered = existing.model_copy(update={"status": "ANSWERED", "answer": answer})
        await self._store.mutate(
            actor="human_operator",
            mutation_type="upsert_question",
            payload={"question": answered.model_dump(mode="json")},
        )
        if answered.node_id and answered.node_id in self._store.snapshot().nodes:
            node = self._store.snapshot().nodes[answered.node_id]
            if node.status == "BLOCKED_HUMAN":
                await self._store.mutate(
                    actor="human_operator",
                    mutation_type="transition_node",
                    payload={"node_id": answered.node_id, "status": "READY"},
                )
        if self._bus is not None:
            await self._bus.publish_topic(
                "escalation.answered",
                {"question_id": question_id, "node_id": answered.node_id, "answer": answer},
            )
        await self._drain_backlog_if_slot_free()
        return answered

    async def expire_overdue_questions(self, *, now: datetime | None = None) -> list[str]:
        """Handle questions exceeding `question_timeout_s` per configured `timeout_policy`."""
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(seconds=self._timeout_s)
        expired_ids: list[str] = []

        for qid, q in list(self._store.snapshot().open_questions.items()):
            if q.status != "OPEN" or q.created_at > cutoff:
                continue
            expired_ids.append(qid)
            if self._timeout_policy == "apply_recommendation" and q.options_ru:
                idx = min(max(0, q.recommendation_idx), len(q.options_ru) - 1)
                await self.answer_question(qid, q.options_ru[idx])
            else:
                updated = q.model_copy(update={"status": "EXPIRED"})
                await self._store.mutate(
                    actor="escalation_timer",
                    mutation_type="upsert_question",
                    payload={"question": updated.model_dump(mode="json")},
                )
        await self._drain_backlog_if_slot_free()
        return expired_ids

    def format_question_card_ru(self, q: OpenQuestion) -> str:
        """Format concise Russian escalation card for Telegram / Web UI / CLI (§23, LP-03)."""
        opts = "\n".join(
            f"  {i + 1}. {opt}" + (" [Рекомендуется]" if i == q.recommendation_idx else "")
            for i, opt in enumerate(q.options_ru)
        )
        node_tag = f" (узел `{q.node_id}`)" if q.node_id else ""
        return (
            f"ВОПРОС [{q.id}]{node_tag} от `{q.author_agent}`:\n"
            f"{q.question_ru}\n"
            f"Варианты:\n{opts}\n"
            f"Влияние: {q.impact_ru}"
        )

    async def _publish_human_question(self, req: EscalationRequest) -> OpenQuestion:
        qid = f"Q_{uuid.uuid4().hex[:8]}"
        q_obj = OpenQuestion(
            id=qid,
            node_id=req.node_id,
            author_agent=req.author_agent,
            question_ru=req.question_ru,
            options_ru=req.options_ru,
            recommendation_idx=req.recommendation_idx,
            impact_ru=req.impact_ru,
            status="OPEN",
        )
        await self._store.mutate(
            actor=req.author_agent,
            mutation_type="upsert_question",
            payload={"question": q_obj.model_dump(mode="json")},
        )
        if self._bus is not None:
            await self._bus.publish_topic(
                "escalation.opened",
                {
                    "question_id": q_obj.id,
                    "node_id": q_obj.node_id,
                    "card_ru": self.format_question_card_ru(q_obj),
                },
            )
        return q_obj

    async def _drain_backlog_if_slot_free(self) -> None:
        while self._backlog:
            open_count = sum(
                1 for q in self._store.snapshot().open_questions.values() if q.status == "OPEN"
            )
            if open_count >= self._max_open:
                break
            nxt = self._backlog.popleft()
            await self._publish_human_question(nxt)
