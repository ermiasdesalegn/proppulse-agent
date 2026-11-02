"""A plan caps how many background jobs an agency may queue a day (app/quota.py, JobQueue.enqueue). Real database, rolled back."""
import uuid

import pytest

from app.jobs import SCHEDULED, JobQueue, JobQuotaExceeded
from app.quota import PLAN_DAILY_JOBS
from app.tenancy import DEFAULT_ORG_ID
from tests.jobs_helpers import app_of, run_queued
from tests.test_channels_api import agency, world  # noqa: F401  (a fixture)

ADMIN = {"X-Admin-Key": "test-admin-key"}


async def fill(db_conn, org, n, kind="test.filler"):
    await db_conn.execute("insert into jobs (org_id, kind, dedupe_key, status) select %s, %s, 'k' || g, 'done' from generate_series(1, %s) g", (org, kind, n))


async def test_the_job_over_the_plans_number_is_refused_for_that_agency_only(repo, db_conn):
    queue = JobQueue(repo.pool)
    busy, quiet = (await agency(db_conn, "job-busy"))[0], (await agency(db_conn, "job-quiet"))[0]
    limit = PLAN_DAILY_JOBS["free"]
    await fill(db_conn, busy, limit - 1)
    last = await queue.enqueue(busy, "test.one_more", dedupe_key="last")
    assert last.created, "the last job of the day still fits"
    with pytest.raises(JobQuotaExceeded) as over:
        await queue.enqueue(busy, "test.one_more", dedupe_key="too-many")
    assert over.value.limit == limit and str(limit) in str(over.value)
    again = await queue.enqueue(busy, "test.one_more", dedupe_key="last")
    assert (again.id, again.created) == (last.id, False), "asking again for a job already queued is not a new job"
    assert (await queue.enqueue(quiet, "test.one_more", dedupe_key="fine")).created, "another agency is unaffected"


async def test_the_operators_agency_and_the_platforms_own_upkeep_are_not_capped(repo, db_conn):
    queue = JobQueue(repo.pool)
    await fill(db_conn, DEFAULT_ORG_ID, PLAN_DAILY_JOBS["free"] + 5)
    assert (await queue.enqueue(DEFAULT_ORG_ID, "test.operator", dedupe_key="x")).created
    capped = (await agency(db_conn, "job-upkeep"))[0]
    await fill(db_conn, capped, PLAN_DAILY_JOBS["free"])
    for kind in ("keys.rewrap", "keys.rotate_data_key", "audit.export"):
        assert (await queue.enqueue(capped, kind, dedupe_key="upkeep")).created, kind
    with pytest.raises(JobQuotaExceeded):
        await queue.enqueue(capped, "test.ordinary", dedupe_key="y")
    await fill(db_conn, capped, 1, kind="keys.rewrap")  # upkeep already queued today does not count either
    with pytest.raises(JobQuotaExceeded):
        await queue.enqueue(capped, "test.ordinary", dedupe_key="z")


async def test_a_bigger_plan_and_a_new_day_have_more_room(repo, db_conn):
    queue = JobQueue(repo.pool)
    pro = (await agency(db_conn, "job-pro"))[0]
    await db_conn.execute("update organizations set plan = 'pro' where id = %s", (pro,))
    await fill(db_conn, pro, PLAN_DAILY_JOBS["free"] + 10)
    assert (await queue.enqueue(pro, "test.more", dedupe_key="a")).created
    old = (await agency(db_conn, "job-yesterday"))[0]
    await fill(db_conn, old, PLAN_DAILY_JOBS["free"])
    await db_conn.execute("update jobs set created_at = now() - interval '30 hours' where org_id = %s", (old,))
    assert (await queue.enqueue(old, "test.more", dedupe_key="b")).created, "yesterday's jobs are not today's"


async def test_a_cron_call_skips_the_agency_at_its_number_and_says_so(world, db_conn):  # noqa: F811
    full, fine = (await agency(db_conn, "cron-full"))[0], (await agency(db_conn, "cron-fine"))[0]
    await fill(db_conn, full, PLAN_DAILY_JOBS["free"])
    state = app_of(world.client).state
    state.settings = state.settings.model_copy(update={"cron_secret": "test-cron-secret"})
    r = await world.client.post("/api/cron/tour-reminders", headers={"X-Cron-Secret": "test-cron-secret"})
    assert r.status_code == 200, r.text
    body = r.json()
    slugs = {j["organization"] for j in body["jobs"]}
    refused = {x["organization"] for x in body["refused"]}
    slug_of = {r["id"]: r["slug"] for r in await world.repo.as_platform().organizations_by_id([full, fine, DEFAULT_ORG_ID])}
    assert slug_of[full] in refused and slug_of[fine] in slugs and slug_of[full] not in slugs
    assert "background jobs today" in body["refused"][0]["reason"]
    assert "proactive.tour_reminders" in SCHEDULED
    assert all(d["org_id"] != full for d in await run_queued(world.client))


async def test_the_usage_endpoint_counts_the_jobs_of_the_day(world, db_conn):  # noqa: F811
    from tests.test_channels_api import bearer

    owner = uuid.uuid4()
    org, _ = await agency(db_conn, "job-meter", (owner, "owner"))
    await fill(db_conn, org, 12)
    await fill(db_conn, org, 3, kind="audit.export")
    body = (await world.client.get("/api/usage", headers=bearer(owner))).json()
    assert body["jobs_today"] == 12 and body["daily_job_limit"] == PLAN_DAILY_JOBS["free"]
    assert app_of(world.client)
