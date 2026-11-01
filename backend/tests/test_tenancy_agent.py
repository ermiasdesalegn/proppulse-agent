"""What the agent, the event stream and the model-call log keep apart between organizations."""
import uuid

from langchain_core.messages import AIMessage

from app.calllog import to_row
from app.chat import Inbound
from app.events import EventBus, sse_frames
from app.tenancy import DEFAULT_ORG_ID, thread_key, use_tenant
from tests.agent_helpers import FakeGateway, ext, make_chat

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()


def tg(text="hello", user="777001"):
    return Inbound(channel="telegram", external_id=user, chat_id=int(user), text=text)


async def make_org(db_conn, label):
    cur = await db_conn.execute("insert into organizations (slug, name) values (%s, %s) returning id", (f"agent-{label}-{uuid.uuid4().hex[:6]}", label))
    return (await cur.fetchone())["id"]


async def saved_messages(agent, thread_id):
    state = await agent.graph.aget_state({"configurable": {"thread_id": thread_key(thread_id)}})
    return [m.content for m in (state.values.get("messages") or [])]


def test_the_original_organization_keeps_bare_thread_ids_and_every_other_is_prefixed():
    assert thread_key("tg:1") == "tg:1", "no organization in scope: the original one"
    with use_tenant(DEFAULT_ORG_ID):
        assert thread_key("tg:1") == "tg:1"
    with use_tenant(ORG_A):
        assert thread_key("tg:1") == f"org:{ORG_A}:tg:1"
    assert thread_key("tg:1", ORG_B) == f"org:{ORG_B}:tg:1", "an explicit organization wins"


async def test_two_agencies_sharing_a_chat_id_never_share_a_conversation(spy_repo, db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    chat, _, agent = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(6)]))
    with use_tenant(a):
        await agent.add_message("tg:424242", AIMessage(content="private to A"))
    with use_tenant(b):
        assert await saved_messages(agent, "tg:424242") == [], "B's chat with the same id starts empty"
    with use_tenant(a):
        assert await saved_messages(agent, "tg:424242") == ["private to A"]
    assert await saved_messages(agent, "tg:424242") == [], "and so does the original organization's"


async def test_the_same_telegram_user_is_a_separate_buyer_with_a_separate_transcript_at_each_agency(spy_repo, db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    chat, services, _ = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(6)]))
    with use_tenant(a):
        out_a = await chat.handle_inbound(tg("I want a flat in Bole"))
    with use_tenant(b):
        out_b = await chat.handle_inbound(tg("hello"))
        mine = await spy_repo.list_messages(out_b.buyer_id)
    assert out_a.buyer_id != out_b.buyer_id
    assert [m["content"] for m in mine if m["role"] == "buyer"] == ["hello"], "B never sees what the same person told A"
    with use_tenant(a):
        assert await spy_repo.get_buyer(out_b.buyer_id) is None


async def test_conversation_locks_and_takeover_notes_are_kept_per_agency(spy_repo, db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    chat, *_ = make_chat(spy_repo, FakeGateway())
    with use_tenant(a):
        lock_a = chat._lock("tg:1")
    with use_tenant(b):
        lock_b = chat._lock("tg:1")
        assert lock_b is not lock_a, "one agency's busy chat does not make the other's wait"


# ---- events and model calls carry their organization ----

def test_events_are_tagged_with_the_organization_that_published_them():
    bus = EventBus()
    with use_tenant(ORG_A):
        mine = bus.publish("message", text="a")
    with use_tenant(ORG_B):
        theirs = bus.publish("message", text="b")
    original = bus.publish("message", text="default")
    assert (mine["org"], theirs["org"], original["org"]) == (str(ORG_A), str(ORG_B), str(DEFAULT_ORG_ID))


async def test_a_tab_is_sent_only_its_own_agencys_events():
    bus = EventBus()
    with use_tenant(ORG_A):
        bus.publish("message", text="for A")
    with use_tenant(ORG_B):
        bus.publish("message", text="for B")

    async def gone() -> bool:
        return True

    for org, other in ((ORG_A, "for B"), (ORG_B, "for A")):
        frames = "".join([f async for f in sse_frames(bus, is_disconnected=gone, org=str(org))])
        assert other not in frames and ("for A" if org == ORG_A else "for B") in frames
    everything = "".join([f async for f in sse_frames(bus, is_disconnected=gone)])
    assert "for A" in everything and "for B" in everything, "without a filter the bus is unchanged"


def test_a_model_call_is_recorded_for_the_organization_it_was_made_for():
    with use_tenant(ORG_A):
        assert to_row({"operation": "chat"})["org_id"] == str(ORG_A)
    assert to_row({"operation": "chat"})["org_id"] == str(DEFAULT_ORG_ID)


async def test_stored_events_remember_their_organization(db_conn):
    """The durable bus stores the organization in a column and returns it on read, so another instance filters the same way."""
    from app.events import DurableEventBus

    org = await make_org(db_conn, "durable")
    await db_conn.execute("insert into event_log (kind, data, org_id) values ('message', '{\"text\": \"x\"}'::jsonb, %s)", (org,))
    row = await (await db_conn.execute("select id, kind, data, created_at, org_id from event_log where org_id = %s", (org,))).fetchone()
    event = DurableEventBus._row_to_event(row)
    assert event["org"] == str(org) and event["text"] == "x"
