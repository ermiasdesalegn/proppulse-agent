"""Channel-agnostic conversation handling: one inbound message in, at most one reply out.

Telegram and the web chat simulator both go through here, so takeover, transcripts and
per-buyer ordering behave identically on every channel.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import BrokerAgent
from app.agent.nodes import TROUBLE
from app.config import Settings
from app.repository import Repository
from app.services import Services

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # Telegram rejects messages over 4096 characters


class Conflict(Exception):
    """The request is valid but not allowed in the buyer's current state."""


@dataclass
class Inbound:
    channel: Literal["telegram", "web"]
    external_id: str  # Telegram user id, or the web simulator session id
    chat_id: int | str | None = None
    kind: Literal["text", "voice"] = "text"
    text: str = ""
    file_id: str | None = None
    mime_type: str = "audio/ogg"

    @property
    def thread_id(self) -> str:
        return f"{'tg' if self.channel == 'telegram' else 'web'}:{self.external_id}"

    @property
    def telegram_id(self) -> int | None:
        return int(self.external_id) if self.channel == "telegram" and self.external_id.isdigit() else None


@dataclass
class Outcome:
    buyer_id: str
    reply: str | None
    takeover: bool = False
    error: bool = False
    state: dict = field(default_factory=dict)


def chunks(text: str, size: int = TELEGRAM_LIMIT) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class ChatService:
    def __init__(self, repo: Repository, agent: BrokerAgent, services: Services, settings: Settings):
        self.repo, self.agent, self.services, self.settings = repo, agent, services, settings
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    # ---------------- inbound ----------------

    async def handle_inbound(self, inbound: Inbound) -> Outcome:
        buyer = await self.repo.get_or_create_buyer(inbound.thread_id, inbound.telegram_id)
        # One turn at a time per buyer: a second message waits, so replies never overlap or reorder.
        async with self._lock(inbound.thread_id):
            buyer = await self.repo.get_buyer(buyer["id"]) or buyer  # re-read: takeover may have flipped while we waited
            shown = inbound.text if inbound.kind == "text" else "[voice note]"
            stored = await self.repo.add_message(
                buyer["id"], "buyer", shown, source=inbound.kind, telegram_file_id=inbound.file_id
            )
            if buyer["human_takeover"]:
                return await self._while_broker_has_the_chat(buyer, inbound, shown)
            return await self._agent_turn(buyer, inbound, stored)

    async def _while_broker_has_the_chat(self, buyer: dict, inbound: Inbound, shown: str) -> Outcome:
        await self.agent.add_message(
            inbound.thread_id, HumanMessage(content=shown, additional_kwargs={"source": inbound.kind})
        )
        chat = self.settings.demo_fallback_telegram_chat_id
        if chat:
            who = buyer.get("full_name") or inbound.thread_id
            await self._best_effort(self.services.telegram.send_message(chat, f"{who}: {shown}"))
        return Outcome(buyer_id=str(buyer["id"]), reply=None, takeover=True)

    async def _agent_turn(self, buyer: dict, inbound: Inbound, stored: dict) -> Outcome:
        buyer_id = str(buyer["id"])
        payload = (
            {"kind": "voice", "file_id": inbound.file_id, "mime_type": inbound.mime_type}
            if inbound.kind == "voice"
            else {"kind": "text", "text": inbound.text}
        )
        typing = asyncio.create_task(self._typing(inbound)) if inbound.channel == "telegram" else None
        try:
            result = await self.agent.handle(
                buyer_id=buyer_id, thread_id=inbound.thread_id, inbound=payload, chat_id=inbound.chat_id
            )
            reply, state, error = result.reply, result.state, False
        except Exception:
            log.exception("agent turn failed for %s", inbound.thread_id)
            reply, state, error = TROUBLE, {}, True
        finally:
            if typing:
                typing.cancel()

        if inbound.kind == "voice" and state.get("messages"):
            spoken = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
            if spoken is not None and spoken.content:
                await self.repo.update_message_content(stored["id"], str(spoken.content))

        if reply:
            await self.repo.add_message(buyer_id, "agent", reply)
            if inbound.channel == "telegram" and inbound.chat_id is not None:
                await self._send_telegram(inbound.chat_id, reply)
        return Outcome(buyer_id=buyer_id, reply=reply, takeover=bool(state.get("human_takeover")), error=error, state=state)

    async def _typing(self, inbound: Inbound) -> None:
        while True:
            await self._best_effort(self.services.telegram.send_chat_action(inbound.chat_id))
            await asyncio.sleep(4)  # Telegram shows the indicator for about 5 seconds

    async def _send_telegram(self, chat_id: int | str, text: str) -> None:
        for part in chunks(text):
            await self._best_effort(self.services.telegram.send_message(chat_id, part))

    @staticmethod
    async def _best_effort(coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("non-critical call failed")

    # ---------------- broker actions ----------------

    async def set_takeover(self, buyer_id: str, on: bool) -> dict:
        buyer = await self.repo.set_human_takeover(buyer_id, on)
        note = "Broker took over this chat." if on else "Broker handed the chat back to the assistant."
        await self.repo.add_message(buyer_id, "broker", note, source="system")
        return buyer

    async def broker_reply(self, buyer_id: str, text: str) -> dict:
        buyer = await self.repo.get_buyer(buyer_id)
        if buyer is None:
            raise LookupError(f"buyer {buyer_id} not found")
        if not buyer["human_takeover"]:
            raise Conflict("Turn on takeover before replying as the broker.")
        async with self._lock(buyer["thread_id"]):
            if buyer["telegram_id"] is not None:
                # Let a delivery failure surface: the broker must know the buyer did not receive it.
                for part in chunks(text):
                    await self.services.telegram.send_message(buyer["telegram_id"], part)
            await self.agent.add_message(buyer["thread_id"], AIMessage(content=text, name="human_broker"))
            return await self.repo.add_message(buyer_id, "broker", text)
