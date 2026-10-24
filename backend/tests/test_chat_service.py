"""ChatService on the real database (rolled back) with a scripted model and mock Telegram."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.chat import Conflict, Inbound, chunks
from app.services.telegram import MockTelegramService, TelegramError
from tests.agent_helpers import FakeGateway, ext, make_chat, make_services

HELLO = [ext("small_talk") for _ in range(6)]


def tg(text="hello", user="777001", **kw):
    return Inbound(channel="telegram", external_id=user, chat_id=int(user), text=text, **kw)


def web(text="hello", session="session-abc12345"):
    return Inbound(channel="web", external_id=session, text=text)


async def transcript(repo, buyer_id):
    return [(m["role"], m["content"], m["source"]) for m in await repo.list_messages(buyer_id)]


async def thread_messages(agent, thread_id):
    return (await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})).values["messages"]


# ---------------- normal turns ----------------

async def test_telegram_turn_replies_shows_typing_and_records_the_transcript(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway(list(HELLO)))
    out = await chat.handle_inbound(tg("hello"))

    assert out.reply == "LLM-REPLY" and not out.takeover and not out.error
    assert [(m["chat_id"], m["text"]) for m in services.telegram.messages] == [(777001, "LLM-REPLY")]
    assert (777001, "typing") in services.telegram.actions
    buyer = await spy_repo.get_buyer_by_thread("tg:777001")
    assert buyer["telegram_id"] == 777001
    assert await transcript(spy_repo, buyer["id"]) == [("buyer", "hello", "text"), ("agent", "LLM-REPLY", "text")]


async def test_web_turn_returns_the_reply_without_touching_telegram(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway(list(HELLO)))
    out = await chat.handle_inbound(web("hi there"))
    assert out.reply == "LLM-REPLY"
    assert services.telegram.messages == [] and services.telegram.actions == []
    assert (await spy_repo.get_buyer_by_thread("web:session-abc12345"))["telegram_id"] is None


async def test_voice_note_transcript_is_saved_over_the_placeholder(spy_repo):
    gw = FakeGateway(list(HELLO), transcript="I want a three bedroom in Bole")
    chat, services, _ = make_chat(spy_repo, gw, services=make_services(files={"VF1": b"ogg"}))
    out = await chat.handle_inbound(Inbound(channel="telegram", external_id="777002", chat_id=777002, kind="voice", file_id="VF1"))
    buyer = await spy_repo.get_buyer_by_thread("tg:777002")
    rows = await spy_repo.list_messages(buyer["id"])
    assert (rows[0]["role"], rows[0]["content"], rows[0]["source"], rows[0]["telegram_file_id"]) == ("buyer", "I want a three bedroom in Bole", "voice", "VF1")
    assert out.reply == "LLM-REPLY"


async def test_a_crashing_turn_still_answers_the_buyer(spy_repo):
    chat, services, agent = make_chat(spy_repo, FakeGateway(list(HELLO)))

    async def boom(**kw):
        raise RuntimeError("database down")

    agent.handle = boom
    out = await chat.handle_inbound(tg())
    assert out.error and "trouble" in out.reply
    assert "trouble" in services.telegram.messages[0]["text"]


async def test_one_turn_per_buyer_at_a_time(spy_repo):
    log = []

    class Slow(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            log.append("start")
            await asyncio.sleep(0.05)
            log.append("end")
            return await super().generate_structured(prompt, schema, system=system)

    chat, _, _ = make_chat(spy_repo, Slow(list(HELLO)))
    await asyncio.gather(chat.handle_inbound(tg("first")), chat.handle_inbound(tg("second")))
    assert log == ["start", "end", "start", "end"], "two turns for one buyer must never overlap"


def test_long_replies_are_split_for_telegram():
    parts = chunks("x" * 9000)
    assert [len(p) for p in parts] == [4000, 4000, 1000] and "".join(parts) == "x" * 9000


# ---------------- human takeover ----------------

async def test_takeover_stores_the_message_without_running_the_agent(spy_repo):
    gw = FakeGateway(list(HELLO))
    chat, services, agent = make_chat(spy_repo, gw, demo_fallback_telegram_chat_id="999")
    buyer = await spy_repo.get_or_create_buyer("tg:777003", 777003)
    await chat.set_takeover(str(buyer["id"]), True)

    out = await chat.handle_inbound(tg("can you lower the price?", user="777003"))
    assert out.takeover and "will reply to you personally" in out.reply, "the buyer is told once that the broker has the chat"
    assert gw.calls == [], "no model call while the broker has the chat"
    msgs = await thread_messages(agent, "tg:777003")
    assert isinstance(msgs[-1], HumanMessage) and msgs[-1].content == "can you lower the price?"
    assert [m["chat_id"] for m in services.telegram.messages] == ["999", 777003], "the broker is notified, and the buyer is told once that the broker has the chat"
    assert "has this chat" in services.telegram.messages[1]["text"] or "will reply to you personally" in services.telegram.messages[1]["text"]
    rows = await transcript(spy_repo, buyer["id"])
    assert rows[0][1] == "Broker took over this chat." and rows[1] == ("buyer", "can you lower the price?", "text")


async def test_voice_note_during_takeover_is_kept_for_the_broker_to_play(spy_repo):
    chat, _, agent = make_chat(spy_repo, FakeGateway(list(HELLO)))
    buyer = await spy_repo.get_or_create_buyer("tg:777004", 777004)
    await chat.set_takeover(str(buyer["id"]), True)
    await chat.handle_inbound(Inbound(channel="telegram", external_id="777004", chat_id=777004, kind="voice", file_id="VF9"))
    row = next(m for m in await spy_repo.list_messages(buyer["id"]) if m["role"] == "buyer")
    assert (row["content"], row["source"], row["telegram_file_id"]) == ("[voice note]", "voice", "VF9")


async def test_broker_reply_reaches_the_buyer_and_the_agents_memory(spy_repo):
    chat, services, agent = make_chat(spy_repo, FakeGateway(list(HELLO)))
    buyer = await spy_repo.get_or_create_buyer("tg:777005", 777005)
    await chat.set_takeover(str(buyer["id"]), True)

    row = await chat.broker_reply(str(buyer["id"]), "I can do 27M if you sign this week.")
    assert row["role"] == "broker"
    assert [(m["chat_id"], m["text"]) for m in services.telegram.messages] == [(777005, "I can do 27M if you sign this week.")]
    last = (await thread_messages(agent, "tg:777005"))[-1]
    assert isinstance(last, AIMessage) and last.name == "human_broker"


async def test_broker_reply_needs_takeover_and_a_real_buyer(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway(list(HELLO)))
    buyer = await spy_repo.get_or_create_buyer("tg:777006", 777006)
    with pytest.raises(Conflict):
        await chat.broker_reply(str(buyer["id"]), "hello?")
    with pytest.raises(LookupError):
        await chat.broker_reply("00000000-0000-0000-0000-000000000000", "hello?")
    assert services.telegram.messages == []


async def test_a_failed_delivery_is_not_recorded_as_sent(spy_repo):
    class Down(MockTelegramService):
        async def send_message(self, *a, **k):
            raise TelegramError("blocked by user")

    services = make_services()
    services.telegram = Down()
    chat, _, agent = make_chat(spy_repo, FakeGateway(list(HELLO)), services=services)
    buyer = await spy_repo.get_or_create_buyer("tg:777007", 777007)
    await chat.set_takeover(str(buyer["id"]), True)
    with pytest.raises(TelegramError):
        await chat.broker_reply(str(buyer["id"]), "are you there?")
    assert all(role != "broker" or source == "system" for role, _, source in await transcript(spy_repo, buyer["id"]))


async def test_after_handback_the_agent_sees_everything_said_during_takeover(spy_repo):
    gw = FakeGateway(list(HELLO))
    chat, _, _ = make_chat(spy_repo, gw)
    await chat.handle_inbound(web("hello"))
    buyer = await spy_repo.get_buyer_by_thread("web:session-abc12345")
    bid = str(buyer["id"])

    await chat.set_takeover(bid, True)
    await chat.handle_inbound(web("can you do 28M?"))
    await chat.broker_reply(bid, "Yes, I can negotiate it down to 28M.")
    await chat.set_takeover(bid, False)

    out = await chat.handle_inbound(web("great, thanks"))
    assert out.reply == "LLM-REPLY"
    prompt = gw.prompts("structured")[-1]
    assert "can you do 28M?" in prompt and "Yes, I can negotiate it down to 28M." in prompt, "no contradicting the broker"


async def test_set_takeover_unknown_buyer(spy_repo):
    chat, _, _ = make_chat(spy_repo, FakeGateway())
    with pytest.raises(LookupError):
        await chat.set_takeover("00000000-0000-0000-0000-000000000000", True)


async def test_the_buyer_is_told_once_not_after_every_message_and_again_after_a_new_takeover(spy_repo):
    gw = FakeGateway(list(HELLO))
    chat, services, _ = make_chat(spy_repo, gw, demo_fallback_telegram_chat_id="999")
    buyer = await spy_repo.get_or_create_buyer("tg:777010", 777010)
    await chat.set_takeover(str(buyer["id"]), True)
    for text in ("hello?", "anyone there?", "what are you saying"):
        await chat.handle_inbound(tg(text, user="777010"))
    to_buyer = [m for m in services.telegram.messages if m["chat_id"] == 777010]
    assert len(to_buyer) == 1, "one acknowledgement, never a repeat of what the buyer wrote"
    assert len([m for m in services.telegram.messages if m["chat_id"] == "999"]) == 3, "the broker still gets every message, once each"
    assert gw.calls == []
    await chat.set_takeover(str(buyer["id"]), False)
    await chat.set_takeover(str(buyer["id"]), True)
    await chat.handle_inbound(tg("hello again", user="777010"))
    assert len([m for m in services.telegram.messages if m["chat_id"] == 777010]) == 2


async def test_the_brokers_own_chat_never_gets_the_buyers_words_echoed_back(spy_repo):
    """In a one-person demo the broker and the buyer are the same Telegram account: relaying to the broker would repeat everything to the buyer."""
    chat, services, _ = make_chat(spy_repo, FakeGateway(list(HELLO)), broker_telegram_chat_id="777011")
    buyer = await spy_repo.get_or_create_buyer("tg:777011", 777011)
    await chat.set_takeover(str(buyer["id"]), True)
    await chat.handle_inbound(tg("book a site visit for option 1", user="777011"))
    texts = [m["text"] for m in services.telegram.messages]
    assert not any("book a site visit" in t for t in texts), texts
    assert len(texts) == 1 and "personally" in texts[0]


async def test_the_broker_chat_setting_is_used_for_the_relay_as_well(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway(list(HELLO)), broker_telegram_chat_id="555", demo_fallback_telegram_chat_id="")
    buyer = await spy_repo.get_or_create_buyer("tg:777012", 777012)
    await chat.set_takeover(str(buyer["id"]), True)
    await chat.handle_inbound(tg("can you lower the price?", user="777012"))
    relayed = [m for m in services.telegram.messages if m["chat_id"] == "555"]
    assert len(relayed) == 1 and relayed[0]["text"].endswith(": can you lower the price?")


async def test_the_acknowledgement_follows_the_buyers_language_and_reaches_the_web_simulator(spy_repo):
    chat, _, _ = make_chat(spy_repo, FakeGateway(list(HELLO)))
    buyer = await spy_repo.get_or_create_buyer("web:session-xyz98765", None)
    await chat.set_takeover(str(buyer["id"]), True)
    out = await chat.handle_inbound(web("ዋጋውን ቀንሱልኝ", session="session-xyz98765"))
    assert out.takeover and out.reply and "አስተላልፌያለሁ" in out.reply
