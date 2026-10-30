"""Telegram Human-in-the-Loop bridge with allowlist auth, throttling, and 64B callbacks (§26)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

from orchestra.config.constants import (
    TG_CALLBACK_DATA_LIMIT,
    TG_EDIT_THROTTLE_S,
    TG_MESSAGE_LIMIT,
)

if TYPE_CHECKING:
    from orchestra.comms.escalation import EscalationManager
    from orchestra.inference.key_pool import KeyPoolManager
    from orchestra.state.mental_map import OpenQuestion
    from orchestra.state.state_store import StateStore
    from orchestra.swarm.orchestrator import SwarmOrchestrator


class TelegramSenderTransport(Protocol):
    """Transport abstraction backed by `aiogram.Bot` in live mode or fake sender in tests."""

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
    ) -> int:
        """Send a message with optional `(label, callback_data)` inline buttons; return msg_id."""
        ...

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit an existing status message in place."""
        ...


def split_telegram_message(text: str, *, limit: int = TG_MESSAGE_LIMIT) -> list[str]:
    """Split oversized Russian SITREP messages cleanly on newline boundaries (TG-06)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append("".join(current))
                current = []
                cur_len = 0
            for idx in range(0, len(line), limit):
                chunks.append(line[idx : idx + limit])
            continue
        if cur_len + len(line) > limit and current:
            chunks.append("".join(current))
            current = [line]
            cur_len = len(line)
        else:
            current.append(line)
            cur_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def encode_callback_data(question_id: str, option_index: int) -> str:
    """Encode inline button callback strictly within Telegram's 64-byte UTF-8 cap (TG-05)."""
    raw = f"q:{question_id}:{option_index}"
    encoded = raw.encode("utf-8")
    if len(encoded) > TG_CALLBACK_DATA_LIMIT:
        short_qid = question_id[:40]
        raw = f"q:{short_qid}:{option_index}"
    return raw


def decode_callback_data(data: str) -> tuple[str, int] | None:
    """Decode `q:{question_id}:{option_index}` callback string."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "q" or not parts[2].isdigit():
        return None
    return (parts[1], int(parts[2]))


class AiogramBotTransport:
    """Live `aiogram.Bot` implementation of `TelegramSenderTransport`."""

    def __init__(self, bot_token: str) -> None:
        """Initialize aiogram Bot and inline keyboard types lazily with `bot_token`."""
        from aiogram import Bot

        self._bot = Bot(token=bot_token)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
    ) -> int:
        """Send message with optional inline keyboard via aiogram."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup: InlineKeyboardMarkup | None = None
        if buttons:
            rows = [
                [InlineKeyboardButton(text=label, callback_data=cb_data)]
                for label, cb_data in buttons
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
        sent = await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        return int(sent.message_id)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit message text via aiogram."""
        await self._bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)

    async def close(self) -> None:
        """Close underlying aiogram HTTP session."""
        await self._bot.session.close()


class TelegramBridge:
    """Russian-language Telegram operator bridge enforcing `allowed_user_ids` (TG-01..11)."""

    def __init__(
        self,
        *,
        allowed_user_ids: list[int] | set[int],
        transport: TelegramSenderTransport,
        state_store: StateStore,
        escalation_manager: EscalationManager | None = None,
        key_pool: KeyPoolManager | None = None,
        orchestrator: SwarmOrchestrator | None = None,
        edit_throttle_s: float = TG_EDIT_THROTTLE_S,
    ) -> None:
        """Initialize TelegramBridge."""
        self._allowed = {int(uid) for uid in allowed_user_ids}
        self._transport = transport
        self._store = state_store
        self._escalation = escalation_manager
        self._key_pool = key_pool
        self._orchestrator = orchestrator
        self._throttle_s = edit_throttle_s
        self._last_edit_ts: dict[tuple[int, int], float] = {}
        self.unauthorized_attempts: list[int] = []

    def is_authorized(self, user_id: int) -> bool:
        """Return True only when `user_id` is explicitly in `allowed_user_ids` (TG-02)."""
        allowed = user_id in self._allowed
        if not allowed:
            self.unauthorized_attempts.append(user_id)
        return allowed

    async def send_sitrep(self, chat_id: int, text_ru: str) -> list[int]:
        """Send multi-part Russian SITREP splitting cleanly at 4096 chars."""
        msg_ids: list[int] = []
        for chunk in split_telegram_message(text_ru):
            mid = await self._transport.send_message(chat_id, chunk)
            msg_ids.append(mid)
        return msg_ids

    async def throttled_edit_status(self, chat_id: int, message_id: int, text_ru: str) -> bool:
        """Edit pinned progress message at most once every `TG_EDIT_THROTTLE_S` seconds (TG-07)."""
        now = time.monotonic()
        key = (chat_id, message_id)
        last = self._last_edit_ts.get(key, 0.0)
        if (now - last) < self._throttle_s:
            return False
        self._last_edit_ts[key] = now
        await self._transport.edit_message(chat_id, message_id, text_ru[:TG_MESSAGE_LIMIT])
        return True

    async def send_question_card(self, chat_id: int, question: OpenQuestion) -> int:
        """Send Russian L3 escalation card with 64-byte-safe inline option buttons."""
        card = (
            self._escalation.format_question_card_ru(question)
            if self._escalation is not None
            else question.question_ru
        )
        buttons = [
            (f"{idx + 1}. {opt[:32]}", encode_callback_data(question.id, idx))
            for idx, opt in enumerate(question.options_ru)
        ]
        return await self._transport.send_message(
            chat_id,
            card[:TG_MESSAGE_LIMIT],
            buttons=buttons,
        )

    async def handle_command(self, *, user_id: int, chat_id: int, text: str) -> str | None:
        """Process incoming Russian operator slash command; silently ignore unauthorized IDs."""
        if not self.is_authorized(user_id):
            return None

        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].lower() if parts else ""
        snap = self._store.snapshot()

        if cmd in ("/start", "/help"):
            reply = (
                "ORCHESTRA — пульт управления роем агентов.\n"
                "Команды: /status, /map, /keys, /answer <id> <текст>, /stop"
            )
        elif cmd == "/status":
            summary = snap.to_summary_dict()
            reply = (
                f"Статус проекта «{snap.project_id}»:\n"
                f"- Стадия: {snap.stage} (версия карты v{snap.version})\n"
                f"- Узлы: {summary['status_counts']}\n"
                f"- Открытых вопросов: {summary['open_questions']}"
            )
        elif cmd == "/map":
            lines = [f"Ментальная карта «{snap.project_id}»:"]
            for nid, node in sorted(snap.nodes.items()):
                lines.append(f"• {nid} [{node.status}] — {node.title}")
            reply = "\n".join(lines)
        elif cmd == "/keys":
            if self._key_pool is None or self._key_pool.size == 0:
                reply = "Пул API-ключей: режим mock / ключи не заданы."
            else:
                k_lines = ["Состояние пула API-ключей (без секретов):"]
                for k in self._key_pool.snapshot_public():
                    k_lines.append(
                        f"• {k.key_id}: {k.status} "
                        f"(запросов={k.total_requests}, 429={k.total_errors_429})"
                    )
                reply = "\n".join(k_lines)
        elif cmd == "/answer" and len(parts) >= 3 and self._escalation is not None:
            qid, ans = parts[1], parts[2]
            await self._escalation.answer_question(qid, ans)
            reply = f"Ответ на вопрос {qid} принят: «{ans}». Узел разблокирован."
        elif cmd == "/stop":
            if self._orchestrator is not None:
                self._orchestrator.request_shutdown()
            reply = "Запрошена плавная остановка роя с сохранением контрольной точки."
        else:
            reply = f"Неизвестная команда: {cmd}. Используйте /help."

        await self.send_sitrep(chat_id, reply)
        return reply

    async def handle_callback(
        self, *, user_id: int, chat_id: int, data: str
    ) -> dict[str, Any] | None:
        """Process inline button callback `q:{qid}:{idx}` from an authorized operator."""
        if not self.is_authorized(user_id):
            return None
        decoded = decode_callback_data(data)
        if decoded is None or self._escalation is None:
            return None
        qid_prefix, opt_idx = decoded
        snap = self._store.snapshot()
        matched_q = next(
            (q for qid, q in snap.open_questions.items() if qid.startswith(qid_prefix)),
            None,
        )
        if matched_q is None or opt_idx >= len(matched_q.options_ru):
            return None
        chosen = matched_q.options_ru[opt_idx]
        resolved = await self._escalation.answer_question(matched_q.id, chosen)
        await self.send_sitrep(chat_id, f"Выбран вариант для {matched_q.id}: «{chosen}».")
        return {"question_id": resolved.id, "answer": chosen}
