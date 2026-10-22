import pytest

from app.agent.checkpointer import CHECKPOINT_TABLES, open_checkpointer
from app.config import get_settings
from tests.agent_helpers import FakeGateway, ext, make_agent, new_buyer, say_to


async def test_postgres_checkpoints_survive_a_restart_and_are_not_publicly_readable(spy_repo, vector_of):
    cp = await open_checkpointer(get_settings(), allow_fallback=False)
    buyer = await new_buyer(spy_repo, "ckpt")
    config = {"configurable": {"thread_id": buyer["thread_id"]}}
    try:
        assert cp.kind == "postgres"
        vec = await vector_of("Noah Bole Towers 3BR%")
        first = make_agent(spy_repo, FakeGateway([ext(max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole")], vector=vec), checkpointer=cp.saver)
        await say_to(first, buyer, "3BR in Bole, 25M")

        restarted = make_agent(spy_repo, FakeGateway([ext(max_budget_etb=30_000_000)], vector=vec), checkpointer=cp.saver)
        saved = (await restarted.graph.aget_state(config)).values
        assert saved["buyer_preferences"]["max_budget_etb"] == 25_000_000 and saved["active_matches"]

        res = await say_to(restarted, buyer, "make it 30M")
        assert res.state["buyer_preferences"]["min_bedrooms"] == 3, "earlier preferences carried over"
        assert res.state["buyer_preferences"]["max_budget_etb"] == 30_000_000

        async with cp._pool.connection() as conn:
            cur = await conn.execute("select relname from pg_class where relname = any(%s) and not relrowsecurity", (list(CHECKPOINT_TABLES),))
            assert await cur.fetchall() == [], "row level security must be on for every checkpoint table"
    finally:
        async with cp._pool.connection() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await conn.execute(f"delete from {table} where thread_id = %s", (buyer["thread_id"],))
        await cp.aclose()


async def test_unreachable_database_falls_back_to_memory_when_allowed():
    bad = get_settings().model_copy(update={"database_url": "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=2"})
    cp = await open_checkpointer(bad)
    assert cp.kind == "memory"
    with pytest.raises(Exception):
        await open_checkpointer(bad, allow_fallback=False)
