"""Turns raw Telegram updates into Inbound messages, and runs them without blocking the webhook response."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from app.chat import ChatService, Inbound

log = logging.getLogger(__name__)

# Message types we cannot read; the buyer gets a polite "text or voice please" instead of silence.
UNREADABLE_KEYS = ("photo", "sticker", "document", "video", "video_note", "audio", "animation", "contact", "location")


def parse_update(update: dict) -> Inbound | None:
    """Return an Inbound for a private-chat message from a person, or None if the update should be ignored."""
    if callback := update.get("callback_query"):
        return parse_callback(callback)
    message = update.get("message")
    if not message:
        return None
    chat, sender = message.get("chat") or {}, message.get("from") or {}
    if chat.get("type") != "private" or sender.get("is_bot") or "id" not in sender:
        return None

    base = dict(channel="telegram", external_id=str(sender["id"]), chat_id=chat["id"])
    if voice := message.get("voice"):
        return Inbound(**base, kind="voice", file_id=voice["file_id"], mime_type=voice.get("mime_type") or "audio/ogg")
    if (text := message.get("text")) is not None:
        text = text.strip()
        command, _, payload = text.partition(" ")
        if command.split("@")[0] == "/start":
            # A bare /start is a greeting. With a payload it is a tap on a channel ad button.
            return Inbound(**base, kind="text", text="Hello", start_payload=payload.strip() or None)
        return Inbound(**base, kind="text", text=text)
    if any(key in message for key in UNREADABLE_KEYS):
        return Inbound(**base, kind="text", text="")
    return None  # service messages such as "user joined"


def parse_callback(callback: dict) -> Inbound | None:
    """A tap on an inline button. The button carries the sentence the buyer chose, which is then handled as if they had typed it."""
    chat = (callback.get("message") or {}).get("chat") or {}
    sender = callback.get("from") or {}
    data = callback.get("data")
    if chat.get("type") != "private" or sender.get("is_bot") or "id" not in sender or not isinstance(data, str) or not data.strip():
        return None
    return Inbound(channel="telegram", external_id=str(sender["id"]), chat_id=chat["id"], kind="text", text=data.strip()[:200], callback_id=callback.get("id"))


class TelegramUpdateHandler:
    def __init__(self, chat: ChatService, *, remember: int = 2000):
        self._chat = chat
        self._seen: deque[int] = deque(maxlen=remember)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, update: dict) -> bool:
        """Start processing in the background and return at once, so Telegram gets its 200 immediately."""
        update_id = update.get("update_id")
        if update_id is not None:
            if update_id in self._seen:
                return False  # Telegram re-sends an update if it did not see a quick 200
            self._seen.append(update_id)
        task = asyncio.create_task(self.process(update))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def process(self, update: dict) -> None:
        try:
            inbound = parse_update(update)
            if inbound is None:
                log.info("Telegram update %s ignored (%s)", update.get("update_id"), ", ".join(k for k in update if k != "update_id"))
                return
            log.info("Telegram update %s: %s from user %s", update.get("update_id"), inbound.kind, inbound.external_id)
            await self._chat.handle_inbound(inbound)
        except Exception:
            log.exception("failed to process Telegram update %s", update.get("update_id"))

    async def drain(self) -> None:
        """Wait for in-flight work (used in tests and at shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


class TelegramPoller:
    """Long-polls getUpdates. Needs no public URL, so it is the default for local development."""

    def __init__(self, telegram: Any, handler: TelegramUpdateHandler, *, poll_seconds: int = 25, retry_base: float = 2.0):
        self._tg, self._handler, self._poll_seconds = telegram, handler, poll_seconds
        self._retry_base = retry_base
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._tg.delete_webhook()  # getUpdates is refused while a webhook is set
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._handler.drain()

    async def _run(self) -> None:
        offset: int | None = None
        failures = 0
        while True:
            try:
                updates = await self._tg.get_updates(offset, self._poll_seconds)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                log.warning("getUpdates failed (%d in a row)", failures, exc_info=failures == 1)
                await asyncio.sleep(min(self._retry_base**failures, 30))
                continue
            for update in updates:
                offset = update["update_id"] + 1
                self._handler.submit(update)
            await asyncio.sleep(0)  # always give other tasks a turn, even if Telegram answers instantly
