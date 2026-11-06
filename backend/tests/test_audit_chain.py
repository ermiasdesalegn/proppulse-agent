"""The tamper-evident audit trail (migration 030, app/auditchain.py): a hash chain per agency that shows a removed, edited or reordered
entry and where, an export to storage the database cannot reach that is append-only and resumable, and the check that compares the two.

Real database (rolled back); the concurrency test uses its own connections and cleans up after itself.
"""
import asyncio
import json
import uuid

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.auditchain import (
    AuditChainBroken,
    DirectorySink,
    S3Sink,
    SinkConflict,
    audit_export,
    batch_key,
    export_org,
    pending,
    verify_chains,
    verify_export,
)
from app.config import get_settings
from tests.jobs_helpers import app_of, run_queued
from tests.test_channels_api import world  # noqa: F401  (a fixture)

ADMIN = {"X-Admin-Key": "test-admin-key"}


async def org_with_entries(db_conn, n, name="chain"):
    cur = await db_conn.execute("insert into organizations (slug, name) values (%s, %s) returning id", (f"{name}-{uuid.uuid4().hex[:6]}", name))
    org = (await cur.fetchone())["id"]
    await add(db_conn, org, n)
    return org


async def add(db_conn, org, n, start=1):
    for i in range(start, start + n):
        await db_conn.execute("insert into audit_log (org_id, actor_kind, action, target, detail) values (%s, 'system', 'test.change', %s, %s)",
                              (org, f"thing {i}", json.dumps({"n": i})))


async def tamper(db_conn, sql, *params):
    """What the table's owner can do: the guard against updates is a trigger, which the owner may switch off."""
    await db_conn.execute("alter table audit_log disable trigger audit_log_immutable")
    await db_conn.execute(sql, params)
    await db_conn.execute("alter table audit_log enable trigger audit_log_immutable")


# ---------------- the chain ----------------


async def test_every_entry_is_chained_to_the_one_before_it_per_agency(spy_repo, db_conn):
    a, b = await org_with_entries(db_conn, 3), await org_with_entries(db_conn, 2)
    rows = await (await db_conn.execute("select org_id, seq, prev_hash, hash from audit_log where org_id = any(%s) order by org_id, seq", ([a, b],))).fetchall()
    mine = [r for r in rows if r["org_id"] == a]
    assert [r["seq"] for r in mine] == [1, 2, 3] and mine[0]["prev_hash"] == ""
    assert mine[1]["prev_hash"] == mine[0]["hash"] and mine[2]["prev_hash"] == mine[1]["hash"] and len({r["hash"] for r in rows}) == 5
    assert [r["seq"] for r in rows if r["org_id"] == b] == [1, 2], "each agency has its own chain"
    assert [p for p in await verify_chains(spy_repo.pool) if p["org_id"] in (str(a), str(b))] == []


@pytest.mark.parametrize("what, sql, seq, words", [
    ("a middle entry removed", "delete from audit_log where org_id = %s and seq = 3", 4, "entries 3 to 3 are missing"),
    ("the first entry removed", "delete from audit_log where org_id = %s and seq = 1", 2, "entries 1 to 1 are missing"),
    ("an entry edited", "update audit_log set detail = '{\"n\": 99}' where org_id = %s and seq = 2", 2, "edited"),
    ("an actor changed", "update audit_log set actor_kind = 'platform' where org_id = %s and seq = 4", 4, "edited"),
    ("two entries swapped", "update audit_log set seq = case seq when 2 then 3 else 2 end where org_id = %s and seq in (2, 3)", 2, ""),
])
async def test_a_removed_edited_or_reordered_entry_is_reported_with_the_agency_and_the_first_broken_entry(spy_repo, db_conn, what, sql, seq, words):
    org = await org_with_entries(db_conn, 5)
    other = await org_with_entries(db_conn, 2)
    await db_conn.execute("alter table audit_log disable trigger audit_log_immutable")
    await db_conn.execute("drop index audit_log_chain_idx")  # the swap passes through two entries with one number; the owner can do this too
    await db_conn.execute(sql, (org,))
    found = [p for p in await verify_chains(spy_repo.pool) if p["org_id"] in (str(org), str(other))]
    assert len(found) == 1, what
    assert found[0]["org_id"] == str(org) and found[0]["seq"] == seq and words in found[0]["problem"], found


async def test_removing_the_newest_entries_leaves_a_chain_that_still_checks_out(spy_repo, db_conn):
    org = await org_with_entries(db_conn, 4)
    await tamper(db_conn, "delete from audit_log where org_id = %s and seq > 2", org)
    assert [p for p in await verify_chains(spy_repo.pool) if p["org_id"] == str(org)] == [], "which is why the export exists"


async def test_two_writers_at_once_get_one_unbroken_chain():
    url = get_settings().database_url
    setup = await AsyncConnection.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=None)
    org = (await (await setup.execute("insert into organizations (slug, name) values (%s, 'Chain') returning id", (f"chain-race-{uuid.uuid4().hex[:6]}",))).fetchone())["id"]
    one = await AsyncConnection.connect(url, row_factory=dict_row, prepare_threshold=None)
    two = await AsyncConnection.connect(url, row_factory=dict_row, prepare_threshold=None)
    try:
        insert = "insert into audit_log (org_id, actor_kind, action) values (%s, 'system', %s)"
        await one.execute(insert, (org, "test.first"))  # holds the agency's chain until it commits
        second = asyncio.create_task(two.execute(insert, (org, "test.second")))
        await asyncio.sleep(0.3)
        assert not second.done(), "the second writer waits its turn"
        await one.commit()
        await asyncio.wait_for(second, 10)
        await two.commit()
        rows = await (await setup.execute("select seq, action, prev_hash, hash from audit_log where org_id = %s order by seq", (org,))).fetchall()
        assert [(r["seq"], r["action"]) for r in rows] == [(1, "test.first"), (2, "test.second")] and rows[1]["prev_hash"] == rows[0]["hash"]
    finally:
        await one.close()
        await two.close()
        await setup.execute("delete from organizations where id = %s", (org,))
        await setup.close()


async def test_an_entry_an_agency_cannot_change_is_still_refused_for_everyone(db_conn):
    org = await org_with_entries(db_conn, 1)
    with pytest.raises(Exception, match="cannot be changed"):
        async with db_conn.transaction():
            await db_conn.execute("update audit_log set action = 'x.y.z' where org_id = %s", (org,))


# ---------------- the export ----------------


def lines(sink, org):
    return [json.loads(x) for k in sink.keys(f"{org}/") for x in sink.read(k).decode().splitlines()]


async def test_the_export_copies_new_entries_once_in_order_and_resumes(spy_repo, db_conn, tmp_path):
    sink = DirectorySink(tmp_path)
    org = await org_with_entries(db_conn, 5)
    assert await pending(spy_repo.pool, org) == 5
    first = await export_org(spy_repo.pool, sink, org, batch=2)
    assert first["exported"] == 5 and first["last_seq"] == 5
    assert sink.keys(f"{org}/") == [batch_key(org, 1, 2), batch_key(org, 3, 4), batch_key(org, 5, 5)]
    got = lines(sink, org)
    assert [e["seq"] for e in got] == [1, 2, 3, 4, 5] and got[2]["prev_hash"] == got[1]["hash"] and got[0]["action"] == "test.change"

    again = await export_org(spy_repo.pool, sink, org, batch=2)
    assert again["exported"] == 0 and await pending(spy_repo.pool, org) == 0, "running it twice exports nothing new"
    await add(db_conn, org, 2, start=6)
    assert (await export_org(spy_repo.pool, sink, org, batch=2))["exported"] == 2
    assert [e["seq"] for e in lines(sink, org)] == [1, 2, 3, 4, 5, 6, 7]
    assert await verify_export(spy_repo.pool, sink, org) == []


async def test_a_run_that_dies_after_writing_a_batch_writes_the_same_batch_next_time(spy_repo, db_conn, tmp_path):
    real = DirectorySink(tmp_path)
    org = await org_with_entries(db_conn, 3)

    class DiesAfterWriting(DirectorySink):
        def put(self, key, data):
            real.put(key, data)
            raise OSError("the process died before it could say so")

    with pytest.raises(OSError):
        await export_org(spy_repo.pool, DiesAfterWriting(tmp_path), org)
    assert await pending(spy_repo.pool, org) == 3, "it did not remember the batch"
    assert (await export_org(spy_repo.pool, real, org))["exported"] == 3
    assert [e["seq"] for e in lines(real, org)] == [1, 2, 3], "and the batch was not written twice"


async def test_the_sink_never_overwrites_a_batch(tmp_path):
    sink = DirectorySink(tmp_path)
    sink.put("o/000000000001-000000000001.jsonl", b"one\n")
    sink.put("o/000000000001-000000000001.jsonl", b"one\n")
    with pytest.raises(SinkConflict):
        sink.put("o/000000000001-000000000001.jsonl", b"changed\n")


async def test_the_export_will_not_copy_over_a_broken_chain(spy_repo, db_conn, tmp_path):
    sink = DirectorySink(tmp_path)
    org = await org_with_entries(db_conn, 4)
    await export_org(spy_repo.pool, sink, org, batch=2)  # entries 1 to 4 are out
    await add(db_conn, org, 3, start=5)
    await tamper(db_conn, "update audit_log set detail = '{\"n\": 0}' where org_id = %s and seq = 6", org)
    with pytest.raises(AuditChainBroken) as stopped:
        await export_org(spy_repo.pool, sink, org)
    assert stopped.value.problem["seq"] == 6
    assert [e["seq"] for e in lines(sink, org)] == [1, 2, 3, 4], "nothing from the break onwards was copied"


async def test_the_comparison_with_the_export_finds_what_the_chain_alone_cannot(spy_repo, db_conn, tmp_path):
    sink = DirectorySink(tmp_path)
    org = await org_with_entries(db_conn, 4)
    await export_org(spy_repo.pool, sink, org)
    await tamper(db_conn, "delete from audit_log where org_id = %s and seq > 2", org)  # the newest entries are cut off
    assert [p for p in await verify_chains(spy_repo.pool) if p["org_id"] == str(org)] == []
    (cut,) = await verify_export(spy_repo.pool, sink, org)
    assert cut["seq"] == 3 and "removed" in cut["problem"]

    # an owner who rewrites an entry and every hash after it leaves a chain that holds, and the export still disagrees
    other = await org_with_entries(db_conn, 3)
    await export_org(spy_repo.pool, sink, other)
    await tamper(db_conn, "update audit_log set prev_hash = 'x', hash = 'y' where org_id = %s and seq = 3", other)
    (edited,) = await verify_export(spy_repo.pool, sink, other)
    assert edited["seq"] == 3 and "edited" in edited["problem"]


# ---------------- other sinks and the scheduled job ----------------


class NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise NoSuchKey()
        return {"Body": type("B", (), {"read": lambda _s, d=self.objects[Key]: d})()}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        assert Key not in self.objects, "the bucket would refuse a second write"
        self.objects[Key] = Body

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):  # noqa: N803
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)]}


async def test_a_bucket_sink_writes_each_batch_once(spy_repo, db_conn):
    client = FakeS3()
    sink = S3Sink("audit-bucket", "proppulse", client=client)
    org = await org_with_entries(db_conn, 3)
    await export_org(spy_repo.pool, sink, org, batch=2)
    assert sorted(client.objects) == [f"proppulse/{batch_key(org, 1, 2)}", f"proppulse/{batch_key(org, 3, 3)}"]
    assert sink.keys(f"{org}/") == [batch_key(org, 1, 2), batch_key(org, 3, 3)] and await verify_export(spy_repo.pool, sink, org) == []
    sink.put(batch_key(org, 1, 2), client.objects[f"proppulse/{batch_key(org, 1, 2)}"])  # the same bytes again: nothing happens
    with pytest.raises(SinkConflict):
        sink.put(batch_key(org, 1, 2), b"other")


async def test_the_scheduled_export_is_off_without_a_sink_and_copies_one_agencys_trail_with_one(world, db_conn, tmp_path):
    from tests.test_channels_api import agency

    org, _ = await agency(db_conn, "chain-job", (uuid.uuid4(), "owner"))
    await add(db_conn, org, 3)
    state = app_of(world.client).state

    async with world.runtime.scope(org):
        off = await audit_export(state)
    assert off["status"] == "off"

    state.settings = state.settings.model_copy(update={"audit_export_dir": str(tmp_path)})
    async with world.runtime.scope(org):
        assert (await audit_export(state, dry_run=True))["pending"] == 3
        done = await audit_export(state)
    assert done["status"] == "exported" and done["exported"] == 3 and done["sent"] == 3
    assert [e["seq"] for e in lines(DirectorySink(tmp_path), org)] == [1, 2, 3]
    async with world.runtime.scope(org):
        assert (await audit_export(state))["exported"] == 0


async def test_the_cron_route_queues_the_export_once_per_day(world, db_conn, tmp_path):
    from tests.test_channels_api import agency

    org, _ = await agency(db_conn, "chain-cron", (uuid.uuid4(), "owner"))
    await add(db_conn, org, 2)
    app_of(world.client).state.settings = app_of(world.client).state.settings.model_copy(update={"audit_export_dir": str(tmp_path)})
    r = await world.client.post("/api/cron/audit-export", headers={**ADMIN, "X-Org-Id": str(org)})
    assert r.status_code == 200 and r.json()["queued"], r.text
    done = await run_queued(world.client)
    assert [d["result"]["exported"] for d in done if d["org_id"] == org] == [2]
