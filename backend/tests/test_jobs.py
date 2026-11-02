"""The job queue (app/jobs.py): fair claims, the per-agency limit, retries, dead jobs, leases, and every scheduled job safe to run twice.

Most tests run on the suite's one shared connection (rolled back) with a hand-moved clock. The concurrency test needs real parallel
transactions, so it opens its own connections, commits, and removes everything it made.
"""
import asyncio
import random
import uuid
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.jobs import REGISTRY, SCHEDULED, JobQueue, JobWorker, backoff_seconds, period_key
from app.projects import ProjectService
from app.tenancy import DEFAULT_ORG_ID, current_tenant
from tests.agent_helpers import FakeGateway, make_proactive
from tests.test_channels_api import bearer, world  # noqa: F401  (world is a fixture)
from tests.test_proactive import GIFT, NOAH, book, make_buyer, pid, signed

LEASE = timedelta(seconds=60)
ADMIN = {"X-Admin-Key": "test-admin-key"}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest_asyncio.fixture
async def clock():
    return Clock()


@pytest_asyncio.fixture
async def queue(repo, db_conn, clock):
    await db_conn.execute("delete from jobs")  # inside the test's transaction, rolled back afterwards
    await db_conn.execute("delete from org_last_served")
    return JobQueue(repo.pool, per_org_limit=1, lease=LEASE, clock=clock, rng=random.Random(1), secrets=["sup3r-secret-value"])


async def new_org(db_conn, name):
    cur = await db_conn.execute("insert into organizations (slug, name) values (%s, %s) returning id", (f"jobs-{name}-{uuid.uuid4().hex[:6]}", name))
    return (await cur.fetchone())["id"]


def worker_for(queue, registry, state=None, name="w"):
    return JobWorker(queue, state or SimpleNamespace(), registry=registry, name=name)


async def noop(ctx):
    return {"ok": True}


# ---------------- claiming ----------------


async def test_agencies_take_turns_whatever_order_their_jobs_were_queued_in(queue, db_conn):
    a, b, c = [await new_org(db_conn, n) for n in "abc"]
    for org in (a, a, a, a, b, b, c, c):  # agency A queued first and the most
        await queue.enqueue(org, "test.noop")
    served = []
    while (job := await queue.claim("w:0")) is not None:
        served.append(job["org_id"])
        assert await queue.complete(job["id"], "w:0")
    first_round = served[:3]
    assert sorted(first_round) == sorted([a, b, c]), "each agency is served once before any is served twice"
    assert served[3:6] == first_round, "and again in the same order"
    assert served[6:] == [a, a], "what is left belongs to the agency with the most work"


async def test_one_agency_never_holds_more_than_its_limit_of_workers(queue, db_conn):
    a, b = await new_org(db_conn, "a"), await new_org(db_conn, "b")
    for _ in range(3):
        await queue.enqueue(a, "test.noop")
    await queue.enqueue(b, "test.noop")
    first, second = await queue.claim("w:0"), await queue.claim("w:1")
    assert {first["org_id"], second["org_id"]} == {a, b}
    assert await queue.claim("w:2") is None, "A already has one running and B has nothing left"
    a_job = first if first["org_id"] == a else second
    await queue.complete(a_job["id"], a_job["locked_by"])
    assert (await queue.claim("w:2"))["org_id"] == a, "a free slot lets A's next job start"

    queue.per_org_limit = 2
    assert (await queue.claim("w:3"))["org_id"] == a, "with a limit of two A may run a second one"
    assert await queue.claim("w:4") is None


async def test_priority_and_age_order_the_jobs_inside_one_agency(queue, db_conn):
    a = await new_org(db_conn, "a")
    old = await queue.enqueue(a, "test.noop")
    urgent = await queue.enqueue(a, "test.noop", priority=5)
    young = await queue.enqueue(a, "test.noop")
    order = []
    while (job := await queue.claim("w:0")) is not None:
        order.append(job["id"])
        await queue.complete(job["id"], "w:0")
    assert order == [urgent.id, old.id, young.id]


async def test_a_job_queued_for_later_waits(queue, db_conn, clock):
    a = await new_org(db_conn, "a")
    await queue.enqueue(a, "test.noop", delay=timedelta(minutes=5))
    assert await queue.claim("w:0") is None
    clock.advance(minutes=5)
    assert await queue.claim("w:0") is not None


async def test_the_same_dedupe_key_queues_one_job(queue, db_conn):
    a, b = await new_org(db_conn, "a"), await new_org(db_conn, "b")
    first = await queue.enqueue(a, "test.noop", {"n": 1}, dedupe_key="2026-11-02")
    again = await queue.enqueue(a, "test.noop", {"n": 2}, dedupe_key="2026-11-02")
    other_agency = await queue.enqueue(b, "test.noop", dedupe_key="2026-11-02")
    other_kind = await queue.enqueue(a, "test.other", dedupe_key="2026-11-02")
    assert first.created and not again.created and again.id == first.id
    assert other_agency.created and other_kind.created
    assert (await queue.get(first.id))["payload"] == {"n": 1}, "the first one is kept as it was"
    assert (await queue.counts())["queued"] == 3


# ---------------- failing ----------------


def test_the_backoff_doubles_up_to_its_cap_with_jitter():
    rng = random.Random(3)
    for attempt in range(1, 12):
        ceiling = min(3600.0, 10.0 * 2 ** (attempt - 1))
        values = [backoff_seconds(attempt, rng=rng) for _ in range(200)]
        assert all(ceiling / 2 <= v <= ceiling for v in values), attempt
        assert max(values) - min(values) > ceiling / 4, "jittered, not all the same"
    assert backoff_seconds(30, rng=random.Random(0)) <= 3600


async def test_a_failure_is_retried_after_the_backoff_then_dies_after_its_last_attempt(queue, db_conn, clock):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.flaky", max_attempts=3)

    for attempt in (1, 2):
        claimed = await queue.claim("w:0")
        assert claimed["attempts"] == attempt
        assert await queue.fail(job.id, "w:0", ValueError("calendar timed out")) == "failed"
        row = await queue.get(job.id)
        wait = (row["run_after"] - clock.now).total_seconds()
        ceiling = 10 * 2 ** (attempt - 1)
        assert ceiling / 2 <= wait <= ceiling and row["last_error"] == "ValueError: calendar timed out"
        assert await queue.claim("w:0") is None, "not before its back-off"
        clock.advance(seconds=wait)

    await queue.claim("w:0")
    assert await queue.fail(job.id, "w:0", ValueError("still down")) == "dead"
    clock.advance(days=1)
    assert await queue.claim("w:0") is None, "a dead job is never picked up again on its own"
    assert (await queue.get(job.id))["finished_at"] is not None


async def test_an_operator_retry_brings_a_dead_job_back_with_all_its_attempts(queue, db_conn):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.flaky", max_attempts=1)
    await queue.claim("w:0")
    await queue.fail(job.id, "w:0", "boom")
    assert (await queue.retry(job.id))["status"] == "queued"
    claimed = await queue.claim("w:0")
    assert claimed["id"] == job.id and claimed["attempts"] == 1
    assert await queue.retry(job.id) is None, "a running job is not retried"


async def test_errors_are_stored_without_secrets(queue, db_conn):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.flaky")
    await queue.claim("w:0")
    await queue.fail(job.id, "w:0", RuntimeError("bot sup3r-secret-value said no, token 123456789:" + "A" * 35))
    error = (await queue.get(job.id))["last_error"]
    assert "sup3r-secret-value" not in error and "A" * 35 not in error and error.startswith("RuntimeError")


async def test_a_job_with_no_code_is_dead_at_once(queue, db_conn):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.nobody_wrote_this")
    await worker_for(queue, {}).run_one()
    row = await queue.get(job.id)
    assert row["status"] == "dead" and "no code is registered" in row["last_error"]


async def test_only_the_worker_holding_the_job_may_finish_it(queue, db_conn):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.noop")
    await queue.claim("w:0")
    assert not await queue.complete(job.id, "intruder") and not await queue.heartbeat(job.id, "intruder")
    assert await queue.fail(job.id, "intruder", "x") is None
    assert await queue.complete(job.id, "w:0")


# ---------------- leases and crashes ----------------


async def test_a_heartbeat_keeps_a_long_job_from_being_taken_away(queue, db_conn, clock):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.noop")
    await queue.claim("w:0")
    for _ in range(5):
        clock.advance(seconds=40)
        assert await queue.heartbeat(job.id, "w:0")
        assert await queue.reap_expired() == []
    clock.advance(seconds=61)
    assert [r["id"] for r in await queue.reap_expired()] == [job.id]


async def test_a_worker_that_dies_mid_job_is_replaced_and_the_job_finishes(queue, db_conn, clock):
    a = await new_org(db_conn, "a")
    attempts, started = [], asyncio.Event()

    async def hangs_the_first_time(ctx):
        attempts.append(ctx.job["attempts"])
        if len(attempts) == 1:
            started.set()
            await asyncio.Event().wait()  # never returns: the worker is killed here
        return {"finished_by": ctx.job["locked_by"]}

    registry = {"test.slow": hangs_the_first_time}
    job = await queue.enqueue(a, "test.slow")
    claimed = await queue.claim("a:0")
    crashed = asyncio.create_task(worker_for(queue, registry, name="a").execute(claimed, "a:0"))
    await started.wait()
    crashed.cancel()
    with suppress(asyncio.CancelledError):
        await crashed
    row = await queue.get(job.id)
    assert row["status"] == "running" and row["locked_by"] == "a:0", "nobody heard the crash"
    assert await worker_for(queue, registry, name="b").run_one("b:0") is None, "the job is still leased"

    clock.advance(seconds=61)
    assert [(r["id"], r["status"]) for r in await queue.reap_expired()] == [(job.id, "queued")]
    await worker_for(queue, registry, name="b").run_one("b:0")
    row = await queue.get(job.id)
    assert row["status"] == "done" and row["attempts"] == 2 and row["result"] == {"finished_by": "b:0"} and attempts == [1, 2]
    assert not await queue.complete(job.id, "a:0"), "the dead worker's late answer is refused"


async def test_a_job_that_keeps_killing_its_worker_ends_up_dead(queue, db_conn, clock):
    a = await new_org(db_conn, "a")
    job = await queue.enqueue(a, "test.crashy", max_attempts=2)
    for _ in range(2):
        await queue.claim("w:0")
        clock.advance(seconds=61)
        await queue.reap_expired()
    assert (await queue.get(job.id))["status"] == "dead"


# ---------------- the agency comes from the row ----------------


async def test_a_job_runs_as_the_agency_it_was_queued_for_whatever_its_payload_says(queue, db_conn):
    a, b = await new_org(db_conn, "a"), await new_org(db_conn, "b")
    seen = []

    async def record(ctx):
        seen.append(current_tenant().org_id)
        return {}

    await queue.enqueue(a, "test.who", {"org_id": str(b)})
    await worker_for(queue, {"test.who": record}).run_one()
    assert seen == [a]


async def test_jobs_are_hidden_from_agency_roles(db_conn):
    await db_conn.execute("set local role app_user")
    try:
        for table in ("jobs", "org_last_served"):
            with pytest.raises(Exception, match="permission denied"):
                async with db_conn.transaction():
                    await db_conn.execute(f"select 1 from {table}")
    finally:
        await db_conn.execute("reset role")


# ---------------- many workers at once ----------------


async def test_many_workers_on_their_own_connections_run_each_of_500_jobs_exactly_once():
    url = get_settings().database_url
    setup = await AsyncConnection.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=None)
    tag = uuid.uuid4().hex[:6]
    orgs = [(await (await setup.execute("insert into organizations (slug, name) values (%s, %s) returning id",
                                        (f"jobs-conc-{tag}-{i}", f"Concurrency {i}"))).fetchone())["id"] for i in range(20)]
    pools = [AsyncConnectionPool(url, min_size=1, max_size=4, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None}) for _ in range(4)]
    try:
        for p in pools:
            await p.open(wait=True)
        runs: Counter = Counter()
        running: defaultdict = defaultdict(int)
        peak: defaultdict = defaultdict(int)
        wrong_scope = []
        all_ran = asyncio.Event()
        rng = random.Random(5)

        async def count(ctx):
            if current_tenant().org_id != ctx.org_id:
                wrong_scope.append(ctx.job["id"])
            running[ctx.org_id] += 1
            peak[ctx.org_id] = max(peak[ctx.org_id], running[ctx.org_id])
            await asyncio.sleep(rng.random() * 0.004)
            running[ctx.org_id] -= 1
            runs[ctx.job["id"]] += 1
            if sum(runs.values()) >= 500:
                all_ran.set()
            return {}

        queue = JobQueue(pools[0])
        ids = [(await queue.enqueue(org, "test.count")).id for _ in range(25) for org in orgs]
        workers = [JobWorker(JobQueue(p, per_org_limit=2), SimpleNamespace(), concurrency=3, poll_interval=0.05,
                             registry={"test.count": count}, name=f"w{i}") for i, p in enumerate(pools)]
        for w in workers:
            await w.start()
        with suppress(TimeoutError):
            await asyncio.wait_for(all_ran.wait(), timeout=120)
        for w in workers:
            await w.stop()

        rows = await (await setup.execute("select id, status, attempts from jobs where id = any(%s)", (ids,))).fetchall()
        assert len(runs) == 500 and set(runs.values()) == {1}, "every job ran, and none twice"
        assert {r["status"] for r in rows} == {"done"} and {r["attempts"] for r in rows} == {1}
        assert max(peak.values()) <= 2, "no agency ever had more than its limit running"
        assert wrong_scope == []
    finally:
        await setup.execute("delete from organizations where id = any(%s)", (orgs,))  # their jobs and served marks go with them
        await setup.close()
        for p in pools:
            await p.close()


# ---------------- the scheduled jobs ----------------


def test_every_scheduled_job_is_registered():
    assert set(SCHEDULED) <= set(REGISTRY)
    assert set(SCHEDULED) == {"proactive.daily", "proactive.reverse_rag", "proactive.tour_reminders", "proactive.milestone_reminders",
                              "projects.promotion_ads"}


def test_the_cron_queues_once_per_period_and_a_button_once_per_minute():
    at = datetime(2026, 11, 2, 9, 41, 12, tzinfo=UTC)
    assert period_key("proactive.daily", "cron", at) == "cron:2026-11-02"
    assert period_key("proactive.reverse_rag", "cron", at) == "cron:2026-11-02T09"
    assert period_key("proactive.reverse_rag", "console", at) == "console:2026-11-02T09:41"


@pytest.fixture
def scheduled_world(repo, db_conn, quiet_world):
    from tests.test_projects import Ads, info

    proactive, services, _ = make_proactive(repo, broker_telegram_chat_id="999", telegram_channel_id="@channel")
    projects = ProjectService(repo, FakeGateway([info(), info()]), services, proactive.settings, proactive=proactive, ads=Ads())
    return SimpleNamespace(proactive=proactive, projects=projects, repo=repo), services


async def something_to_send(repo, db_conn):
    """One of everything: a price drop the buyer wants, a visit tomorrow, a payment due soon, a developer's offer."""
    from tests.test_projects import add_project

    abel = await make_buyer(repo, db_conn, "twice")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    await book(repo, db_conn, abel, datetime.now(UTC) + timedelta(hours=6), GIFT)
    await signed(repo, db_conn, abel, GIFT, date.today() + timedelta(days=2))
    await add_project(repo, db_conn, promotion="10% off", promotion_until=(date.today() + timedelta(days=3)).isoformat())


@pytest.mark.parametrize("kind", sorted(SCHEDULED))
async def test_each_scheduled_job_run_twice_messages_once(kind, scheduled_world, queue, repo, db_conn):
    state, services = scheduled_world
    await something_to_send(repo, db_conn)
    worker = JobWorker(queue, state)

    async def run(key):
        job = await queue.enqueue(DEFAULT_ORG_ID, kind, dedupe_key=key)  # the original agency, which the fixtures' services serve
        await worker.run_one()
        return await queue.get(job.id)

    first = await run("first")
    sent = len(services.telegram.messages) + len(getattr(services.telegram, "photos", []))
    events = await count_events(db_conn)
    assert first["status"] == "done" and first["result"]["sent"] >= 1, first

    second = await run("second")  # a retry, a second cron, an operator's replay
    assert second["status"] == "done" and second["result"]["sent"] == 0
    assert len(services.telegram.messages) + len(getattr(services.telegram, "photos", [])) == sent, "nobody was messaged twice"
    assert await count_events(db_conn) == events


async def count_events(db_conn):
    return (await (await db_conn.execute("select count(*) as n from proactive_events where status = 'sent'")).fetchone())["n"]


# ---------------- the API ----------------


async def test_an_agency_admin_sees_its_own_jobs_and_the_operator_sees_all(world, db_conn):
    from tests.test_channels_api import agency

    a_admin, a_viewer, b_admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a, _ = await agency(db_conn, "alpha", (a_admin, "admin"), (a_viewer, "viewer"))
    b, _ = await agency(db_conn, "beta", (b_admin, "admin"))
    await db_conn.execute("delete from jobs")
    queue = JobQueue(world.repo.pool)
    ja, jb = await queue.enqueue(a, "test.noop"), await queue.enqueue(b, "test.noop")

    mine = (await world.client.get("/api/jobs", headers={**bearer(a_admin), "X-Org-Id": str(a)})).json()
    assert [j["id"] for j in mine["jobs"]] == [ja.id] and mine["counts"]["queued"] == 1 and not mine["operator"]
    every = (await world.client.get("/api/jobs", headers=ADMIN)).json()
    assert {ja.id, jb.id} <= {j["id"] for j in every["jobs"]} and every["operator"]
    only_b = (await world.client.get("/api/jobs", params={"org_id": str(b)}, headers=ADMIN)).json()
    assert [j["id"] for j in only_b["jobs"]] == [jb.id]
    sneaky = (await world.client.get("/api/jobs", params={"org_id": str(b)}, headers={**bearer(a_admin), "X-Org-Id": str(a)})).json()
    assert [j["id"] for j in sneaky["jobs"]] == [ja.id], "an agency cannot ask for another agency's jobs"
    assert (await world.client.get("/api/jobs", headers={**bearer(a_viewer), "X-Org-Id": str(a)})).status_code == 403


async def test_only_the_operator_retries_and_only_a_dead_or_failed_job(world, db_conn):
    from tests.test_channels_api import agency

    a_admin = uuid.uuid4()
    a, _ = await agency(db_conn, "alpha", (a_admin, "owner"))
    queue = JobQueue(world.repo.pool)
    job = await queue.enqueue(a, "test.flaky", max_attempts=1)
    await queue.claim("w:0")
    await queue.fail(job.id, "w:0", "boom")

    assert (await world.client.post(f"/api/jobs/{job.id}/retry", headers={**bearer(a_admin), "X-Org-Id": str(a)})).status_code == 403
    r = await world.client.post(f"/api/jobs/{job.id}/retry", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert (await world.client.post(f"/api/jobs/{job.id}/retry", headers=ADMIN)).status_code == 409
    assert (await world.client.post("/api/jobs/999999999/retry", headers=ADMIN)).status_code == 404
    trail = await (await db_conn.execute("select action, actor_kind, target from audit_log where org_id = %s", (a,))).fetchall()
    assert [(t["action"], t["actor_kind"]) for t in trail] == [("job.retry", "platform")] and f"#{job.id}" in trail[0]["target"]
