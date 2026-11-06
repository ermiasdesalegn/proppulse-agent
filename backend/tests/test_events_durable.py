"""The durable bus against a real Postgres: these tests commit what they write and remove it afterwards."""
import asyncio

import pytest_asyncio

from app.config import get_settings
from app.db import make_pool
from app.events import DurableEventBus, sse_frames


@pytest_asyncio.fixture
async def pool():
    pool = make_pool(get_settings())
    await pool.open(wait=True, timeout=20)
    async with pool.connection() as conn:
        await conn.execute("delete from event_log")
    yield pool
    async with pool.connection() as conn:
        await conn.execute("delete from event_log")
    await pool.close()


async def started(pool, **kw):
    bus = DurableEventBus(pool, poll_seconds=0.1, **kw)
    await bus.start()
    return bus


async def next_event(sub, timeout=5.0):
    return await asyncio.wait_for(sub.queue.get(), timeout)


async def test_a_published_event_is_stored_and_reaches_a_subscriber_with_its_database_id(pool):
    bus = await started(pool)
    try:
        sub = await bus.open()
        bus.publish("proactive", what="PRICE_DROP", audience="buyer")
        event = await next_event(sub)
        assert event["kind"] == "proactive" and event["what"] == "PRICE_DROP" and event["id"] > 0 and event["ts"]
        async with pool.connection() as conn:
            row = await (await conn.execute("select kind, data from event_log where id = %s", (event["id"],))).fetchone()
        assert row["kind"] == "proactive" and row["data"] == {"what": "PRICE_DROP", "audience": "buyer"}
    finally:
        await bus.stop()


async def test_two_instances_see_each_others_events_in_the_same_order_with_the_same_ids(pool):
    one, two = await started(pool), await started(pool)
    try:
        sub_one, sub_two = await one.open(), await two.open()
        one.publish("a", n=1)
        two.publish("b", n=2)
        one.publish("c", n=3)
        seen_one = [await next_event(sub_one) for _ in range(3)]
        seen_two = [await next_event(sub_two) for _ in range(3)]
        assert [e["id"] for e in seen_one] == [e["id"] for e in seen_two]
        assert [e["id"] for e in seen_one] == sorted(e["id"] for e in seen_one)
        assert {e["kind"] for e in seen_one} == {"a", "b", "c"}
    finally:
        await one.stop()
        await two.stop()


async def test_history_survives_a_restart_and_a_reconnecting_tab_gets_only_what_it_missed(pool):
    first = await started(pool)
    sub = await first.open()
    for n in range(4):
        first.publish("tick", n=n)
    seen = [await next_event(sub) for _ in range(4)]
    await first.stop()

    second = await started(pool)  # a new process: nothing in memory
    try:
        everything = await second.open(0)
        assert [e["n"] for e in everything.backlog] == [0, 1, 2, 3]
        after_two = await second.open(seen[1]["id"])
        assert [e["n"] for e in after_two.backlog] == [2, 3]
        assert (await second.open(seen[3]["id"])).backlog == []
    finally:
        await second.stop()


async def test_the_replayed_backlog_is_capped_to_the_most_recent_events(pool):
    bus = await started(pool, history=3)
    try:
        sub = await bus.open()
        for n in range(6):
            bus.publish("tick", n=n)
        for _ in range(6):
            await next_event(sub)
        assert [e["n"] for e in (await bus.open(0)).backlog] == [3, 4, 5]
    finally:
        await bus.stop()


async def test_sse_frames_stream_durable_events_without_duplicates(pool):
    bus = await started(pool)
    try:
        bus.publish("old", n=0)
        await asyncio.sleep(0.5)

        async def alive():
            return False

        frames = sse_frames(bus, after_id=0, is_disconnected=alive, ping_seconds=0.05)
        assert "connected" in await frames.__anext__()
        assert '"kind": "old"' in await frames.__anext__()
        await frames.aclose()
    finally:
        await bus.stop()


async def test_publishing_never_raises_when_the_database_is_unreachable():
    class DeadPool:
        def connection(self):
            raise RuntimeError("database down")

    bus = DurableEventBus(DeadPool())
    bus._tasks = [asyncio.create_task(bus._writer())]
    try:
        bus.publish("x")
        await asyncio.sleep(0.2)
        assert bus.dropped == 1
    finally:
        bus._tasks[0].cancel()


async def test_a_full_outbox_drops_events_instead_of_blocking_the_publisher(pool):
    bus = DurableEventBus(pool, outbox=2)  # not started: nothing drains the outbox
    for _ in range(5):
        bus.publish("x")
    assert bus.dropped == 3


async def test_old_events_are_pruned(pool):
    bus = await started(pool, retention_days=1)
    try:
        async with pool.connection() as conn:
            await conn.execute("insert into event_log (kind, created_at) values ('ancient', now() - interval '3 days'), ('fresh', now())")
        await bus._prune()
        async with pool.connection() as conn:
            kinds = [r["kind"] for r in await (await conn.execute("select kind from event_log")).fetchall()]
        assert kinds == ["fresh"]
    finally:
        await bus.stop()


# ---- an event whose id is taken before a later one's but commits after it ----

async def _hold_an_insert(pool):
    """A connection that has inserted an event (so its id is taken) and not committed yet, like a slow writer on another instance."""
    conn = await pool.getconn()
    cur = await conn.execute("insert into event_log (kind, data) values ('late', '{}'::jsonb) returning id")
    return conn, (await cur.fetchone())["id"]


async def test_an_event_that_commits_after_a_later_one_is_still_delivered_in_order(pool):
    bus = await started(pool)
    try:
        sub = await bus.open()
        holder, late_id = await _hold_an_insert(pool)
        bus.publish("after")  # takes the next id and commits at once
        await asyncio.sleep(0.5)
        assert sub.queue.empty(), "the later event waits for the earlier id instead of jumping ahead of it"
        await holder.commit()
        await pool.putconn(holder)
        first, second = await next_event(sub), await next_event(sub)
        assert (first["id"], first["kind"]) == (late_id, "late") and second["kind"] == "after" and second["id"] > late_id
    finally:
        await bus.stop()


async def test_an_id_that_never_commits_holds_delivery_back_only_for_the_grace_period(pool):
    bus = await started(pool, gap_grace=0.5)
    try:
        sub = await bus.open()
        holder, _ = await _hold_an_insert(pool)
        await holder.rollback()  # a failed insert: its id is never used
        await pool.putconn(holder)
        bus.publish("after")
        event = await next_event(sub, timeout=3)
        assert event["kind"] == "after"
    finally:
        await bus.stop()


async def test_a_tab_opened_during_a_gap_gets_the_late_event_too(pool):
    bus = await started(pool)
    try:
        holder, late_id = await _hold_an_insert(pool)
        bus.publish("after")
        await asyncio.sleep(0.5)
        sub = await bus.open()  # opens while the gap is open: its backlog must not run ahead of it
        await holder.commit()
        await pool.putconn(holder)
        got = [await next_event(sub), await next_event(sub)]
        assert [e["kind"] for e in got] == ["late", "after"] and got[0]["id"] == late_id
    finally:
        await bus.stop()
