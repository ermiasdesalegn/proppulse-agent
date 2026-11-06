"""Scheduled jobs run once per agency, each inside its own agency's scope, and one agency's trouble never stops the others."""
import itertools
import uuid

import pytest

from tests.jobs_helpers import run_queued, total
from tests.test_channels_api import TOKEN_A, TOKEN_B, agency, bearer, connect, world  # noqa: F401  (world is a fixture)

NOAH = "Noah Bole Towers 3BR%"
CRON = {"X-Cron-Secret": "cron-secret"}
_chat_ids = itertools.count(990001)

pytestmark = pytest.mark.usefixtures("quiet_world")


async def buyer_in(db_conn, org, tag):
    chat_id = next(_chat_ids)
    await db_conn.execute(
        "insert into buyers_crm (org_id, thread_id, telegram_id, max_budget_etb, min_bedrooms, preferred_sub_cities, deal_stage, full_name, "
        "preference_embedding) values (%s, %s, %s, 25000000, 3, %s, 'QUALIFIED', %s, (select embedding from properties where title like %s limit 1))",
        (org, f"test:{tag}:{chat_id}", chat_id, ["Bole"], tag.title(), NOAH),
    )
    return chat_id


async def drop_the_price(world, db_conn):
    pid = (await (await db_conn.execute("select id from properties where title like %s", (NOAH,))).fetchone())["id"]
    await world.repo.update_property_price(pid, 17_500_000)


async def two_agencies(world, db_conn):
    a_admin, b_admin = uuid.uuid4(), uuid.uuid4()
    a, slug_a = await agency(db_conn, "alpha", (a_admin, "owner"))
    b, slug_b = await agency(db_conn, "beta", (b_admin, "owner"))
    await connect(world, a_admin, TOKEN_A)
    await connect(world, b_admin, TOKEN_B)
    return (a, slug_a, a_admin), (b, slug_b, b_admin)


async def test_a_price_drop_reaches_each_agencys_own_buyers_through_that_agencys_own_bot(world, db_conn):
    (a, slug_a, _), (b, slug_b, _) = await two_agencies(world, db_conn)
    chat_a, chat_b = await buyer_in(db_conn, a, "abel"), await buyer_in(db_conn, b, "bekele")
    await drop_the_price(world, db_conn)

    r = await world.client.post("/api/cron/reverse-rag-alerts", headers=CRON)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] and {slug_a, slug_b} <= {j["organization"] for j in body["jobs"]}, "one job per agency"
    assert world.sent_by(TOKEN_A) == [], "the request only queues; the workers send"
    done = await run_queued(world.client)
    assert {r["status"] for r in done} == {"done"} and total(done) == 2 and total(done, "failed") == 0
    assert [m["chat_id"] for m in world.sent_by(TOKEN_A)] == [chat_a], "agency A's bot messaged only A's buyer"
    assert [m["chat_id"] for m in world.sent_by(TOKEN_B)] == [chat_b], "agency B's bot messaged only B's buyer"
    assert world.base.telegram.messages == [], "the platform's own bot sent nothing for either agency"

    again = await world.client.post("/api/cron/reverse-rag-alerts", headers=CRON)
    assert not any(j["created"] for j in again.json()["jobs"]), "the same cron hour is queued once per agency"
    assert await run_queued(world.client) == []
    assert len(world.sent_by(TOKEN_A)) == 1 and len(world.sent_by(TOKEN_B)) == 1


async def test_a_failing_agency_is_reported_and_the_others_still_run(world, db_conn):
    (a, slug_a, _), (b, slug_b, _) = await two_agencies(world, db_conn)
    await buyer_in(db_conn, a, "abel")
    chat_b = await buyer_in(db_conn, b, "bekele")
    await drop_the_price(world, db_conn)

    real = world.runtime.activate

    async def broken_for_a(org_id):
        if org_id == a:
            raise RuntimeError("this agency's channels are broken")
        return await real(org_id)

    world.runtime.activate = broken_for_a
    await world.client.post("/api/cron/reverse-rag-alerts", headers=CRON)
    done = {r["org_id"]: r for r in await run_queued(world.client)}
    assert done[a]["status"] == "failed" and "RuntimeError" in done[a]["last_error"], "A's job waits for its retry"
    assert done[b]["status"] == "done"
    assert [m["chat_id"] for m in world.sent_by(TOKEN_B)] == [chat_b], "B was not held up by A"
    assert world.sent_by(TOKEN_A) == []


async def test_suspended_agencies_are_skipped(world, db_conn):
    (a, slug_a, _), (b, slug_b, _) = await two_agencies(world, db_conn)
    await db_conn.execute("update organizations set status = 'suspended' where id = %s", (a,))
    body = (await world.client.post("/api/cron/reverse-rag-alerts?dry_run=true", headers=CRON)).json()
    assert slug_a not in body["organizations"] and slug_b in body["organizations"]


async def test_a_member_runs_a_job_for_their_own_agency_only(world, db_conn):
    (a, _, a_admin), (b, _, _) = await two_agencies(world, db_conn)
    chat_a, _ = await buyer_in(db_conn, a, "abel"), await buyer_in(db_conn, b, "bekele")
    await drop_the_price(world, db_conn)
    r = await world.client.post("/api/cron/reverse-rag-alerts", headers=bearer(a_admin))
    assert r.status_code == 200 and [j["organization"] for j in r.json()["jobs"]] == [(await (await db_conn.execute("select slug from organizations where id = %s", (a,))).fetchone())["slug"]]
    assert total(await run_queued(world.client)) == 1
    assert [m["chat_id"] for m in world.sent_by(TOKEN_A)] == [chat_a] and world.sent_by(TOKEN_B) == []


async def test_who_may_start_a_job(world, db_conn):
    viewer, agent = uuid.uuid4(), uuid.uuid4()
    await agency(db_conn, "crew", (viewer, "viewer"), (agent, "agent"))
    post = lambda path, headers=None: world.client.post(path, headers=headers or {})  # noqa: E731
    assert (await post("/api/cron/reminders", bearer(viewer))).status_code == 403
    assert (await post("/api/cron/reminders", bearer(agent))).status_code == 200
    assert (await post("/api/cron/reminders")).status_code == 401
    assert (await post("/api/cron/reminders", {"X-Cron-Secret": "wrong"})).status_code == 401
    assert (await post("/api/cron/reminders", {"X-Admin-Key": "wrong"})).status_code == 401
    assert (await post("/api/cron/reminders", {"X-Admin-Key": "test-admin-key"})).status_code == 200
    assert (await post("/api/cron/scrape-market?dry_run=true", bearer(agent))).status_code == 403, "market data is for admins"


async def test_the_admin_key_runs_for_the_original_agency_or_the_one_named(world, db_conn):
    (a, slug_a, _), _ = await two_agencies(world, db_conn)
    admin = {"X-Admin-Key": "test-admin-key"}
    original = (await world.client.post("/api/cron/reverse-rag-alerts?dry_run=true", headers=admin)).json()
    assert "organizations" not in original, "a single agency: the job's own answer, unchanged"
    named = await world.client.post("/api/cron/reverse-rag-alerts?dry_run=true", headers={**admin, "X-Org-Id": str(a)})
    assert named.status_code == 200
    assert (await world.client.post("/api/cron/reverse-rag-alerts", headers={**admin, "X-Org-Id": str(uuid.uuid4())})).status_code == 404


async def test_the_reminder_jobs_also_run_per_agency(world, db_conn):
    await two_agencies(world, db_conn)
    for path in ("tour-reminders", "milestone-reminders", "reminders"):
        r = await world.client.post(f"/api/cron/{path}?dry_run=true", headers=CRON)
        assert r.status_code == 200 and len(r.json()["organizations"]) >= 3, path
