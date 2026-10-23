"""Live activity: the event bus, SSE framing, and the events the agent, chat service and proactive jobs publish."""
import json

import httpx
import pytest

from app.events import EventBus, excerpt, format_sse, sse_frames, summarize_update
from app.main import create_app
from tests.agent_helpers import FakeGateway, ext, make_agent, make_chat, make_proactive, new_buyer, say_to
from tests.test_proactive import NOAH, make_buyer, pid

BOLE_3BR = dict(max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3 bedroom apartment in Bole")


# ---------------- bus ----------------

def test_a_payload_field_named_kind_cannot_replace_the_event_type():
    event = EventBus().publish("proactive", what="PRICE_DROP", audience="buyer")
    assert event["kind"] == "proactive" and event["what"] == "PRICE_DROP"
    assert EventBus().publish("x", **{"id": 99, "kind": "y"})["id"] == 1, "reserved fields are never overridden by a payload"


def test_events_get_increasing_ids_timestamps_and_the_given_fields():
    bus = EventBus()
    a, b = bus.publish("node", node="router", ms=12), bus.publish("message", role="buyer")
    assert (a["id"], b["id"]) == (1, 2) and a["kind"] == "node" and a["node"] == "router" and a["ts"].endswith("+00:00")


def test_publishing_without_subscribers_is_fine_and_history_is_capped():
    bus = EventBus(history=5)
    for i in range(12):
        bus.publish("x", n=i)
    assert [e["n"] for e in bus.subscribe(0).backlog] == [7, 8, 9, 10, 11]


def test_a_new_subscriber_replays_only_what_it_missed():
    bus = EventBus()
    for i in range(4):
        bus.publish("x", n=i)
    assert [e["id"] for e in bus.subscribe(2).backlog] == [3, 4]
    assert bus.subscribe(4).backlog == [] and [e["id"] for e in bus.subscribe(0).backlog] == [1, 2, 3, 4]


async def test_live_events_reach_every_subscriber_and_closing_unsubscribes():
    bus = EventBus()
    one, two = bus.subscribe(), bus.subscribe()
    bus.publish("x", n=1)
    assert (await one.queue.get())["n"] == 1 and (await two.queue.get())["n"] == 1
    one.close()
    assert bus.subscribers == 1
    bus.publish("x", n=2)
    assert one.queue.empty() and (await two.queue.get())["n"] == 2


def test_a_stalled_subscriber_is_dropped_instead_of_blocking_publishers():
    bus = EventBus()
    stalled = bus.subscribe()
    for i in range(600):  # its queue holds 500
        bus.publish("x", n=i)
    assert bus.subscribers == 0 and stalled.queue.qsize() == 500


def test_summaries_leave_out_embeddings_and_transcripts():
    update = {"query_embedding": [0.1] * 768, "messages": ["secret transcript"], "intent": "search", "buyer_preferences": {"max_budget_etb": 1.0},
              "active_matches": [{"id": "x", "title": "Unit", "price_etb": 5.0, "similarity": 0.7, "developer_id": "d"}],
              "search_scope": {"relaxation_step": 1, "max_relaxation_steps": 2, "effective_max_budget_etb": 9.0, "effective_sub_cities": [], "extra": 1},
              "reply": "word " * 100, "comparison_matrix": {"A": {}, "B": {}}}
    out = summarize_update(update)
    assert "query_embedding" not in out and "messages" not in out and "extra" not in out["search_scope"]
    assert out["matches"] == [{"title": "Unit", "price_etb": 5.0, "similarity": 0.7}] and out["compared"] == ["A", "B"]
    assert len(out["reply"]) <= 200 and out["intent"] == "search"
    assert summarize_update(None) == {} and summarize_update({}) == {}


def test_excerpt_collapses_whitespace_and_truncates():
    assert excerpt("a   b\n c") == "a b c" and excerpt("x" * 300).endswith("…") and len(excerpt("x" * 300)) == 200 and excerpt(None) == ""


# ---------------- SSE framing ----------------

async def test_sse_frames_replay_history_then_stream_live_without_duplicates_and_ping_when_idle():
    bus = EventBus()
    bus.publish("node", node="intake")
    stop = {"now": False}

    async def disconnected():
        return stop["now"]

    frames = sse_frames(bus, after_id=0, is_disconnected=disconnected, ping_seconds=0.05)
    first = [await frames.__anext__(), await frames.__anext__()]
    assert first[0].startswith("retry: 3000") and first[1].startswith("id: 1\nevent: node\ndata: ")
    assert json.loads(first[1].split("data: ", 1)[1])["node"] == "intake"
    assert await frames.__anext__() == ": ping\n\n"
    bus.publish("message", role="buyer")
    live = await frames.__anext__()
    while live == ": ping\n\n":
        live = await frames.__anext__()
    assert live.startswith("id: 2\nevent: message")
    stop["now"] = True
    with pytest.raises(StopAsyncIteration):
        while True:
            await frames.__anext__()
    assert bus.subscribers == 0, "the subscription is released when the tab goes away"


def test_format_sse_is_valid_event_stream_text():
    frame = format_sse({"id": 7, "kind": "takeover", "on": True})
    assert frame.endswith("\n\n") and frame.splitlines()[:2] == ["id: 7", "event: takeover"]


async def test_the_stream_endpoint_needs_the_admin_key(spy_repo):
    chat, _, _ = make_chat(spy_repo, FakeGateway(), admin_api_key="test-admin-key")
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.events = chat.settings, EventBus()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/stream/events")).status_code == 401
        assert (await c.get("/api/stream/events", headers={"X-Admin-Key": "wrong"})).status_code == 401


# ---------------- what the agent, chat service and jobs publish ----------------

def nodes(bus):
    return [e["node"] for e in bus.subscribe(0).backlog if e["kind"] == "node"]


async def test_a_search_turn_publishes_each_node_in_order_with_timings_and_results(spy_repo, vector_of):
    bus = EventBus()
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "events")
    agent._deps.events = bus
    await say_to(agent, buyer, "3BR in Bole, 25M")

    events = bus.subscribe(0).backlog
    assert [e["kind"] for e in events][0] == "turn_started" and events[-1]["kind"] == "turn_finished"
    assert nodes(bus) == ["intake", "router", "discovery", "recommend"]
    by_node = {e["node"]: e for e in events if e["kind"] == "node"}
    assert by_node["router"]["intent"] == "search" and by_node["router"]["preferences"]["max_budget_etb"] == 25_000_000
    assert by_node["discovery"]["matches"][0]["title"] == "Noah Bole Towers 3BR Apartment"
    assert all(isinstance(e["ms"], int) and e["ms"] >= 0 for e in by_node.values()) and events[-1]["ms"] >= 0
    assert {e["buyer_id"] for e in events} == {str(buyer["id"])} and "query_embedding" not in json.dumps(events)


async def test_relaxation_shows_up_as_extra_discovery_steps(spy_repo, vector_of):
    bus = EventBus()
    gw = FakeGateway([ext(max_budget_etb=16_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole")], vector=await vector_of("Gift Bole Court%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "events2")
    agent._deps.events = bus
    await say_to(agent, buyer, "3 bedroom in Bole under 16M")
    assert nodes(bus) == ["intake", "router", "discovery", "relaxer", "discovery", "recommend"]
    steps = [e["search_scope"]["relaxation_step"] for e in bus.subscribe(0).backlog if e["kind"] == "node" and e["node"] == "relaxer"]
    assert steps == [1]


async def test_a_failing_turn_publishes_turn_failed_and_still_raises(spy_repo):
    class Broken(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            raise RuntimeError("boom")

    bus = EventBus()
    agent, buyer = make_agent(spy_repo, Broken([ext()])), await new_buyer(spy_repo, "events3")
    agent._deps.events = bus
    agent.graph  # compiled graph is unchanged; the router swallows model errors, so break the repo instead

    async def explode(*a, **k):
        raise RuntimeError("db down")

    spy_repo.get_buyer = explode
    with pytest.raises(RuntimeError):
        await say_to(agent, buyer, "hello")
    kinds = [e["kind"] for e in bus.subscribe(0).backlog]
    assert kinds[0] == "turn_started" and kinds[-1] == "turn_failed"


async def test_chat_and_takeover_activity_is_published(spy_repo):
    bus = EventBus()
    chat, _, _ = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(3)]))
    chat.events = bus
    chat.agent._deps.events = bus
    from app.chat import Inbound

    await chat.handle_inbound(Inbound(channel="web", external_id="session-evt00001", text="hello there"))
    buyer = await spy_repo.get_buyer_by_thread("web:session-evt00001")
    await chat.set_takeover(str(buyer["id"]), True)
    await chat.broker_reply(str(buyer["id"]), "I'll take it from here.")

    msgs = [(e["role"], e["text"]) for e in bus.subscribe(0).backlog if e["kind"] == "message"]
    assert msgs == [("buyer", "hello there"), ("agent", "LLM-REPLY"), ("broker", "I'll take it from here.")]
    assert [(e["on"]) for e in bus.subscribe(0).backlog if e["kind"] == "takeover"] == [True]


async def test_proactive_sends_are_published(spy_repo, db_conn):

    bus = EventBus()
    proactive, _, _ = make_proactive(spy_repo)
    proactive.events = bus
    await db_conn.execute("update buyers_crm set deal_stage = 'LOST' where thread_id not like 'test:%'")
    await db_conn.execute("delete from proactive_events")
    await make_buyer(spy_repo, db_conn, "evt")
    await spy_repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    await proactive.reverse_rag_alerts()
    sent = [e for e in bus.subscribe(0).backlog if e["kind"] == "proactive"]
    assert [(e["audience"], e["channel"], e["what"]) for e in sent] == [("buyer", "telegram", "PRICE_DROP")] and "dropped from" in sent[0]["text"]
