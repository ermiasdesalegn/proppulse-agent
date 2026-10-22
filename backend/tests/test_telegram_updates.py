import asyncio
import json

import httpx
import pytest

from app.services.telegram import BotApiTelegramService
from app.telegram_updates import TelegramPoller, TelegramUpdateHandler, parse_update


def upd(update_id=1, user=42, chat_type="private", is_bot=False, **message):
    return {"update_id": update_id, "message": {"chat": {"id": user, "type": chat_type}, "from": {"id": user, "is_bot": is_bot}, **message}}


# ---------------- parsing ----------------

def test_text_message():
    i = parse_update(upd(text="  3 bedroom in Bole  "))
    assert (i.channel, i.thread_id, i.chat_id, i.kind, i.text, i.telegram_id) == ("telegram", "tg:42", 42, "text", "3 bedroom in Bole", 42)


@pytest.mark.parametrize("text", ["/start", "/start@PropPulseBot"])
def test_start_command_becomes_a_greeting(text):
    assert parse_update(upd(text=text)).text == "Hello"


def test_voice_message_keeps_the_file_id_and_defaults_the_mime_type():
    i = parse_update(upd(voice={"file_id": "AwAC", "duration": 4}))
    assert (i.kind, i.file_id, i.mime_type) == ("voice", "AwAC", "audio/ogg")
    assert parse_update(upd(voice={"file_id": "x", "mime_type": "audio/mpeg"})).mime_type == "audio/mpeg"


@pytest.mark.parametrize("key", ["photo", "sticker", "document", "video", "video_note", "location"])
def test_unreadable_content_still_gets_an_answer(key):
    i = parse_update(upd(**{key: {"file_id": "x"}}))
    assert i.kind == "text" and i.text == ""


def test_updates_that_must_be_ignored():
    assert parse_update(upd(text="hi", chat_type="group")) is None
    assert parse_update(upd(text="hi", is_bot=True)) is None
    assert parse_update(upd(new_chat_members=[{"id": 1}])) is None
    assert parse_update({"update_id": 2, "edited_message": {"text": "x"}}) is None
    assert parse_update({"update_id": 3, "callback_query": {"data": "x"}}) is None
    assert parse_update({"update_id": 4}) is None


# ---------------- handler ----------------

class RecordingChat:
    def __init__(self, gate=None, fail=False):
        self.seen, self.gate, self.fail = [], gate, fail

    async def handle_inbound(self, inbound):
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("boom")
        self.seen.append(inbound)


async def test_submit_returns_before_the_work_finishes():
    gate = asyncio.Event()
    chat = RecordingChat(gate)
    handler = TelegramUpdateHandler(chat)
    assert handler.submit(upd(1, text="hi")) is True
    assert chat.seen == [], "the webhook must be able to answer 200 while the agent is still working"
    gate.set()
    await handler.drain()
    assert len(chat.seen) == 1


async def test_a_redelivered_update_is_processed_once():
    chat = RecordingChat()
    handler = TelegramUpdateHandler(chat)
    assert handler.submit(upd(7, text="hi")) is True
    assert handler.submit(upd(7, text="hi")) is False
    await handler.drain()
    assert len(chat.seen) == 1


async def test_a_failing_turn_never_escapes_the_handler():
    handler = TelegramUpdateHandler(RecordingChat(fail=True))
    handler.submit(upd(1, text="hi"))
    await handler.drain()  # would raise if the exception leaked


async def test_ignored_updates_do_nothing():
    chat = RecordingChat()
    handler = TelegramUpdateHandler(chat)
    handler.submit(upd(1, text="hi", chat_type="channel"))
    await handler.drain()
    assert chat.seen == []


# ---------------- poller ----------------

class Recorder:
    def __init__(self):
        self.updates = []

    def submit(self, update):
        self.updates.append(update)
        return True

    async def drain(self):
        pass


async def test_poller_clears_the_webhook_then_fetches_and_advances_the_offset():
    requests, batches = [], [[upd(10, text="a"), upd(11, text="b")], [], []]

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        requests.append((req.url.path.rsplit("/", 1)[-1], body))
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": batches.pop(0) if batches else []})
        return httpx.Response(200, json={"ok": True, "result": True})

    recorder = Recorder()
    poller = TelegramPoller(BotApiTelegramService("1:x", transport=httpx.MockTransport(handler)), recorder, poll_seconds=0)
    await poller.start()
    for _ in range(100):
        if len(recorder.updates) == 2 and sum(1 for m, _ in requests if m == "getUpdates") >= 2:
            break
        await asyncio.sleep(0.01)
    await poller.stop()

    assert requests[0][0] == "deleteWebhook"
    polls = [b for m, b in requests if m == "getUpdates"]
    assert "offset" not in polls[0] and polls[1]["offset"] == 12
    assert [u["update_id"] for u in recorder.updates] == [10, 11]


async def test_poller_survives_telegram_errors():
    calls = {"n": 0}

    def handler(req: httpx.Request):
        if req.url.path.endswith("/getUpdates"):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            return httpx.Response(200, json={"ok": True, "result": [upd(5, text="after the error")] if calls["n"] == 2 else []})
        return httpx.Response(200, json={"ok": True, "result": True})

    recorder = Recorder()
    poller = TelegramPoller(BotApiTelegramService("1:x", transport=httpx.MockTransport(handler)), recorder, poll_seconds=0, retry_base=0.01)
    await poller.start()
    for _ in range(200):
        if recorder.updates:
            break
        await asyncio.sleep(0.01)
    await poller.stop()
    assert [u["update_id"] for u in recorder.updates] == [5]
