"""Channel-agnostic conversation handling: one inbound message in, at most one reply out.

Telegram and the web chat simulator both go through here, so takeover, transcripts and
per-buyer ordering behave identically on every channel.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage

from app.ads import BUTTONS, DEEPLINK
from app.agent.graph import BrokerAgent
from app.agent.nodes import option_block
from app.agent.prompts import TRANSCRIBE, UNINTELLIGIBLE
from app.config import Settings
from app.events import excerpt
from app.language import detect_language, msg
from app.projects import PROJECT_LINK
from app.repository import Repository
from app.services import Services

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # Telegram rejects messages over 4096 characters
TAKEOVER_ACK_SECONDS = 1800  # while the broker has a chat, tell the buyer so at most this often


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
    start_payload: str | None = None  # what followed /start, for example "ask_1a2b3c4d" from a channel ad button
    shown_as: str | None = None  # what the transcript shows for a tap, when the default wording would not suit the channel (the website says it in its own words)
    callback_id: str | None = None  # set when the message is a tap on an inline button

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
    quick_replies: list = field(default_factory=list)  # button rows the channel may show under the reply
    message_id: int | None = None  # the stored reply, which the website's file links point at
    attachments: list = field(default_factory=list)  # files the website shows with the reply (Telegram has already been sent them)


def inline_keyboard(rows: list[list[dict]] | None) -> dict | None:
    """Telegram inline buttons from quick-reply rows. The button's data is the sentence it stands for, so a tap needs no stored state."""
    keyboard = []
    for row in rows or []:
        buttons = [{"text": b["label"][:40], "callback_data": b["text"]} for b in row if b.get("text") and len(b["text"].encode("utf-8")) <= 64]
        if buttons:
            keyboard.append(buttons)
    return {"inline_keyboard": keyboard} if keyboard else None


def chunks(text: str, size: int = TELEGRAM_LIMIT) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class ChatService:
    def __init__(self, repo: Repository, agent: BrokerAgent, services: Services, settings: Settings, events: Any = None):
        self.repo, self.agent, self.services, self.settings, self.events = repo, agent, services, settings, events
        self._locks: dict[str, asyncio.Lock] = {}
        self._takeover_acks: dict[str, float] = {}  # thread -> when the buyer was last told the broker has the chat
        self.projects: Any = None  # ProjectService, set by the application

    async def transcribe(self, audio: bytes, mime_type: str) -> str | None:
        """What a voice note recorded on the website says, with the same prompt a Telegram voice note gets; None when it cannot be made out."""
        try:
            text = (await self.agent.gateway.transcribe_audio(audio, mime_type=mime_type, prompt=TRANSCRIBE)).strip()
        except Exception:
            log.exception("web voice note could not be transcribed")
            return None
        return None if not text or UNINTELLIGIBLE in text.upper() else text

    def _emit(self, kind: str, **data: Any) -> None:
        if self.events:
            self.events.publish(kind, **data)

    def _lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    # ---------------- inbound ----------------

    async def handle_inbound(self, inbound: Inbound) -> Outcome:
        if inbound.callback_id:
            await self._best_effort(self.services.telegram.answer_callback(inbound.callback_id))  # stop the button's spinner at once
        buyer = await self.repo.get_or_create_buyer(inbound.thread_id, inbound.telegram_id)
        # One turn at a time per buyer: a second message waits, so replies never overlap or reorder.
        async with self._lock(inbound.thread_id):
            buyer = await self.repo.get_buyer(buyer["id"]) or buyer  # re-read: takeover may have flipped while we waited
            shown = inbound.text if inbound.kind == "text" else "[voice note]"
            if inbound.shown_as:
                shown = inbound.shown_as
            elif inbound.start_payload and (link := DEEPLINK.match(inbound.start_payload)):
                shown = f"Opened a channel ad: {dict(BUTTONS)[link.group(1)]}"
            elif inbound.start_payload and PROJECT_LINK.match(inbound.start_payload):
                shown = "Opened a project ad"
            stored = await self.repo.add_message(
                buyer["id"], "buyer", shown, source=inbound.kind, telegram_file_id=inbound.file_id
            )
            self._emit("message", role="buyer", buyer_id=str(buyer["id"]), message_id=stored["id"], source=inbound.kind,
                       channel=inbound.channel, text=excerpt(shown))
            if buyer["human_takeover"]:
                return await self._while_broker_has_the_chat(buyer, inbound, shown)
            if self.projects is not None:
                about_a_project = await self._project_turn(buyer, inbound)
                if about_a_project is not None:
                    return about_a_project
            if inbound.start_payload:
                opened = await self._from_channel_ad(buyer, inbound, stored)
                if opened is not None:
                    return opened
            return await self._agent_turn(buyer, inbound, stored)

    async def _project_turn(self, buyer: dict, inbound: Inbound) -> Outcome | None:
        """Buttons and names of projects (developers that publish no prices) are handled in code, with no model call."""
        lang = detect_language(inbound.text or "")
        try:
            if inbound.start_payload:
                answer = await self.projects.handle_link(buyer, inbound.start_payload, lang)
            elif inbound.kind == "text":
                answer = await self.projects.handle_text(buyer, inbound.text, lang)
            else:
                return None
        except Exception:
            log.exception("project handling failed; the agent answers instead")
            return None
        if answer is None:
            return None
        return await self._say(buyer, inbound, answer[0], answer[1] or None)

    async def _say(self, buyer: dict, inbound: Inbound, text: str, quick_replies: list | None = None) -> Outcome:
        buyer_id = str(buyer["id"])
        sent = await self.repo.add_message(buyer_id, "agent", text)
        self._emit("message", role="agent", buyer_id=buyer_id, message_id=sent["id"], source="text", channel=inbound.channel, text=excerpt(text))
        if inbound.channel == "telegram" and inbound.chat_id is not None:
            await self._send_telegram(inbound.chat_id, text, quick_replies)
        return Outcome(buyer_id=buyer_id, reply=text, quick_replies=quick_replies or [])

    async def _from_channel_ad(self, buyer: dict, inbound: Inbound, stored: dict) -> Outcome | None:
        """A buyer tapped a button on a channel ad. Load that unit into the conversation so 'option 1' means it."""
        link = DEEPLINK.match(inbound.start_payload or "")
        if link is None:
            return None  # not one of ours: treat it as a plain /start
        action, code = link.groups()
        found = await self.repo.find_property_by_code(code)
        rows = await self.repo.get_properties([found["id"]]) if found else []
        if not rows or rows[0]["status"] != "AVAILABLE":
            return await self._say(buyer, inbound, "That listing is no longer available. Tell me your budget, bedrooms and preferred area and I'll find similar options.")
        unit = rows[0]

        def slim(row: dict, similarity: float) -> dict:
            return {"id": str(row["id"]), "developer_id": str(row["developer_id"]), "title": row["title"], "sub_city": row["sub_city"],
                    "price_etb": float(row["price_etb"]), "bedrooms": row["bedrooms"], "similarity": similarity}

        thread = inbound.thread_id
        if action == "ask":
            tags = (unit.get("vision_amenities") or {}).get("tags") or []
            text = (f"Here are the details.\n\n{option_block(1, unit, ', '.join(tags[:3]))}\n\n"
                    "Would you like to compare it with similar options, or book a site visit?")
            await self.agent.add_message(thread, AIMessage(content=text), active_matches=[slim(unit, 1.0)])
            return await self._say(buyer, inbound, text, [[{"label": "Book a visit", "text": "Book a site visit for option 1"}],
                                                          [{"label": "Phone consultation", "text": "I'd like a phone consultation"}]])

        if action == "cmp":
            similar = await self.repo.similar_properties(unit["id"], threshold=self.settings.similarity_threshold, limit=2)
            more = await self.repo.get_properties([s["id"] for s in similar])
            options = [slim(unit, 1.0)] + [slim(r, float(s["similarity"])) for r, s in zip(more, similar)]
            note = f"You opened {unit['title']}." + (" For comparison I also found similar options." if more else " I found nothing similar to compare it with.")
            await self.agent.add_message(thread, AIMessage(content=note), active_matches=options)
            synthetic = "Compare these options for me."
        else:
            await self.agent.add_message(thread, AIMessage(content=f"You opened {unit['title']}."), active_matches=[slim(unit, 1.0)])
            synthetic = "I'd like to book a site visit for option 1."
        return await self._agent_turn(buyer, replace(inbound, text=synthetic, start_payload=None), stored)

    async def _while_broker_has_the_chat(self, buyer: dict, inbound: Inbound, shown: str) -> Outcome:
        """The agent stays silent and the message is kept for the broker. Two courtesies: the broker is told on Telegram (never back into the
        chat the message came from, which would echo the buyer's own words to them), and the buyer is told once in a while that the broker
        has the chat, so a silence is not mistaken for a broken bot."""
        await self.agent.add_message(
            inbound.thread_id, HumanMessage(content=shown, additional_kwargs={"source": inbound.kind})
        )
        chat = self.settings.broker_telegram_chat_id or self.settings.demo_fallback_telegram_chat_id
        if chat and str(chat) != str(inbound.chat_id):
            who = buyer.get("full_name") or inbound.thread_id
            await self._best_effort(self.services.telegram.send_message(chat, f"{who}: {shown}"))
        now = time.monotonic()
        if now - self._takeover_acks.get(inbound.thread_id, float("-inf")) >= TAKEOVER_ACK_SECONDS:
            self._takeover_acks[inbound.thread_id] = now
            note = msg("broker_has_chat", detect_language(inbound.text or ""), broker=self.settings.broker_name)
            return replace(await self._say(buyer, inbound, note), takeover=True)
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
            reply, state, error = msg("trouble", detect_language(inbound.text)), {}, True
        finally:
            if typing:
                typing.cancel()

        if inbound.kind == "voice" and state.get("messages"):
            spoken = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
            if spoken is not None and spoken.content:
                await self.repo.update_message_content(stored["id"], str(spoken.content))

        quick_replies = [] if error else list(state.get("quick_replies") or [])
        attachments = [] if error or inbound.channel != "web" else list(state.get("attachments") or [])
        sent = None
        if reply:
            sent = await self.repo.add_message(buyer_id, "agent", reply, attachments=attachments)
            self._emit("message", role="agent", buyer_id=buyer_id, message_id=sent["id"], source="text", channel=inbound.channel, text=excerpt(reply))
            if inbound.channel == "telegram" and inbound.chat_id is not None:
                await self._send_telegram(inbound.chat_id, reply, quick_replies)
        if state.get("human_takeover"):
            self._takeover_acks[inbound.thread_id] = time.monotonic()  # this turn already told the buyer the broker is taking over
        return Outcome(buyer_id=buyer_id, reply=reply, takeover=bool(state.get("human_takeover")), error=error, state=state, quick_replies=quick_replies,
                       message_id=sent["id"] if sent else None, attachments=attachments if sent else [])

    async def _typing(self, inbound: Inbound) -> None:
        while True:
            await self._best_effort(self.services.telegram.send_chat_action(inbound.chat_id))
            await asyncio.sleep(4)  # Telegram shows the indicator for about 5 seconds

    async def _send_telegram(self, chat_id: int | str, text: str, quick_replies: list | None = None) -> None:
        parts = chunks(text)
        for i, part in enumerate(parts):
            markup = inline_keyboard(quick_replies) if i == len(parts) - 1 else None  # buttons go under the last part
            await self._best_effort(self.services.telegram.send_message(chat_id, part, reply_markup=markup))

    @staticmethod
    async def _best_effort(coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("non-critical call failed")

    # ---------------- broker actions ----------------

    async def set_takeover(self, buyer_id: str, on: bool) -> dict:
        buyer = await self.repo.set_human_takeover(buyer_id, on)
        self._takeover_acks.pop(str(buyer.get("thread_id")), None)  # a new takeover starts with a fresh acknowledgement
        note = "Broker took over this chat." if on else "Broker handed the chat back to the assistant."
        await self.repo.add_message(buyer_id, "broker", note, source="system")
        self._emit("takeover", buyer_id=str(buyer_id), on=on, name=buyer.get("full_name"))
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
            row = await self.repo.add_message(buyer_id, "broker", text)
            self._emit("message", role="broker", buyer_id=str(buyer_id), message_id=row["id"], source="text", channel="telegram" if buyer["telegram_id"] else "web", text=excerpt(text))
            return row
