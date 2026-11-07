"""Background jobs, queued per agency in Postgres and run by workers inside the app, fairly between agencies.

The timed work (the daily proactive run, price-drop alerts, tour and milestone reminders, promotion ads) used to run inside the cron
request, one agency after another, so one slow agency held up every agency after it. Now the cron call queues one job per agency
and returns; workers claim jobs from the `jobs` table (migration 027) and run each inside its agency's scope.

- **Fair.** A claim takes the agency that was served longest ago (`org_last_served`), then that agency's most urgent, oldest job, and
  never gives one agency more than `per_org_limit` running jobs, so a busy agency cannot take every worker.
- **No double claims.** `for update skip locked`: two workers never get the same row, and neither waits for the other.
- **Leases, not trust.** A running job holds a lease that its worker extends while it works. A worker that dies stops extending it;
  `reap_expired` puts the job back in the queue, so a crash is retried, not lost.
- **Retries.** A failure is retried after an exponential, capped, jittered delay; after `max_attempts` the job is `dead` and waits for
  an operator. The stored error passes through the log redaction, so it holds no secret.
- **At least once.** A job can therefore run twice (a lease that expired under a slow worker, an operator's retry). Every job must be
  safe to run twice; the proactive jobs are, because each message is claimed once (`Repository.claim_event`) before it is sent.
- **The agency comes from the row.** The worker enters `runtime.scope(job.org_id)`; nothing in a job's payload can choose the agency.

The tables belong to the platform (no grant to `app_user`), so the queue uses the pool's own connections, never a tenant's.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.observability import redact
from app.quota import daily_jobs
from app.tenancy import DEFAULT_ORG_ID, use_tenant

log = logging.getLogger(__name__)

STATUSES = ("queued", "running", "done", "failed", "dead")
DUE = ("queued", "failed")  # 'failed' is a job waiting for its retry
ERROR_LIMIT = 500


def backoff_seconds(attempts: int, *, base: float = 10.0, cap: float = 3600.0, rng: random.Random | None = None) -> float:
    """How long to wait before retry number `attempts` (1 after the first failure): base * 2^(attempts-1), capped, then between half
    and all of that ("equal jitter"), so many jobs that failed together do not all come back in the same second."""
    ceiling = min(cap, base * 2 ** max(0, attempts - 1))
    return ceiling / 2 + (rng or random).random() * ceiling / 2


@dataclass(frozen=True)
class Enqueued:
    id: int
    org_id: UUID
    kind: str
    created: bool  # False: the same job (agency, kind, dedupe key) was already queued, and this is that one


# Jobs the platform queues for its own upkeep: never counted against an agency's daily number, never refused.
UNCAPPED_KINDS = frozenset({"keys.rewrap", "keys.rotate_data_key", "audit.export"})


class JobQuotaExceeded(Exception):
    """The agency has queued as many jobs today as its plan allows."""

    def __init__(self, org_id: Any, limit: int):
        super().__init__(f"this agency has queued {limit} background jobs today, the most its plan allows")
        self.org_id, self.limit = org_id, limit


class _OverLimit(Exception):
    """Raised inside a claim's transaction to undo it: another worker took this agency's last free slot first."""


# When `clock` is None the database's own clock is used (`clock_timestamp()`, the time of the statement, not of the transaction),
# so every worker on every machine agrees on what "expired" means. Tests pass a clock to move time by hand.
NOW = "coalesce(%(now)s::timestamptz, clock_timestamp())"


class JobQueue:
    def __init__(
        self, pool: Any, *, per_org_limit: int = 1, lease: timedelta = timedelta(seconds=60), clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None, backoff_base: float = 10.0, backoff_cap: float = 3600.0, secrets: Sequence[str] = (),
    ):
        self._pool = pool
        self.per_org_limit, self.lease, self._clock = per_org_limit, lease, clock
        self._rng = rng or random.Random()
        self._backoff = (backoff_base, backoff_cap)
        self._secrets = list(secrets)
        self.wake = asyncio.Event()  # set by `enqueue` so an idle worker in this process starts at once instead of at its next poll

    def _p(self, **params: Any) -> dict[str, Any]:
        return {"now": self._clock() if self._clock else None, **params}

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[Any]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    yield cur

    async def _one(self, query: str, params: dict[str, Any]) -> dict | None:
        async with self._cursor() as cur:
            await cur.execute(query, params)
            return await cur.fetchone()

    # ---- putting work in ----

    async def enqueue(
        self, org_id: UUID | str, kind: str, payload: dict | None = None, *, dedupe_key: str | None = None, priority: int = 0,
        delay: timedelta = timedelta(0), max_attempts: int = 5,
    ) -> Enqueued:
        """Queue a job for one agency. The same (agency, kind, dedupe key) is queued once: a second call returns the first job,
        whatever state it is in. Without a dedupe key every call queues a new job."""
        key = dedupe_key or uuid4().hex
        params = self._p(org=str(org_id), kind=kind, payload=Jsonb(payload or {}), key=key, priority=priority, delay=delay, max=max_attempts)
        async with self._cursor() as cur:
            if kind not in UNCAPPED_KINDS and UUID(str(org_id)) != DEFAULT_ORG_ID:
                await cur.execute(
                    "select o.plan, (select count(*)::int from jobs j where j.org_id = o.id and j.kind <> all(%(free)s) and j.created_at >= "
                    f"date_trunc('day', {NOW} at time zone 'utc') at time zone 'utc') as today from organizations o where o.id = %(org)s",
                    {**params, "free": sorted(UNCAPPED_KINDS)})
                usage = await cur.fetchone()
                limit = daily_jobs(usage["plan"] if usage else None)
                if usage is not None and usage["today"] >= limit:  # the same job again is not a new one, so it is still answered
                    await cur.execute("select id from jobs where org_id = %(org)s and kind = %(kind)s and dedupe_key = %(key)s", params)
                    seen = await cur.fetchone()
                    if seen is None:
                        raise JobQuotaExceeded(UUID(str(org_id)), limit)
                    return Enqueued(int(seen["id"]), UUID(str(org_id)), kind, False)
            await cur.execute(
                "insert into jobs (org_id, kind, payload, dedupe_key, priority, run_after, max_attempts) "
                f"values (%(org)s, %(kind)s, %(payload)s, %(key)s, %(priority)s, {NOW} + %(delay)s, %(max)s) "
                "on conflict (org_id, kind, dedupe_key) do nothing returning id", params)
            row = await cur.fetchone()
            created = row is not None
            if row is None:
                await cur.execute("select id from jobs where org_id = %(org)s and kind = %(kind)s and dedupe_key = %(key)s", params)
                row = await cur.fetchone()
        assert row is not None
        if created:
            self.wake.set()
        return Enqueued(int(row["id"]), UUID(str(org_id)), kind, created)

    # ---- taking work out ----

    # Whose turn it is: the agencies with work due and a free slot, the one served longest ago first. The agencies with work due are
    # found by skipping through the `jobs_org_due_idx` index one agency at a time, so the cost grows with how many agencies are
    # waiting, not with how many jobs they queued (a sort over every queued row made a claim cost 20 ms at 1,000 queued jobs).
    TURNS = (
        "with recursive due(org_id) as ("
        f"  (select org_id from jobs where status in ('queued', 'failed') and run_after <= {NOW} order by org_id limit 1)"
        "  union all"
        "  select (select j.org_id from jobs j where j.status in ('queued', 'failed') and j.org_id > due.org_id"
        f"          and j.run_after <= {NOW} order by j.org_id limit 1) from due where due.org_id is not null"
        "), busy as ("
        "  select org_id from jobs where status = 'running' group by org_id having count(*) >= %(limit)s"
        ") "
        "select d.org_id from due d left join org_last_served s on s.org_id = d.org_id "
        "where d.org_id is not null and d.org_id not in (select org_id from busy) "
        "order by s.served_at nulls first, d.org_id limit %(orgs)s"
    )
    # That agency's most urgent, oldest due job. `skip locked`: a row another worker is claiming at this moment is passed over, never
    # waited for, and never handed out twice.
    CLAIM = (
        f"update jobs set status = 'running', locked_by = %(worker)s, locked_until = {NOW} + %(lease)s, attempts = attempts + 1, "
        f"started_at = {NOW}, finished_at = null "
        "where id = (select id from jobs where org_id = %(org)s and status in ('queued', 'failed') "
        f"            and run_after <= {NOW} order by priority desc, id limit 1 for update skip locked) "
        "returning *"
    )

    async def claim(self, worker: str) -> dict | None:
        """Take the next job for `worker`, or None when nothing is due.

        TURNS lists whose turn it is and CLAIM locks that agency's next job; when another worker holds all of it, the next agency in
        line is tried. The running limit is then made exact: the claim locks its agency's `org_last_served` row (which also marks the
        agency as just served) and counts again. Two workers that claimed jobs of the same agency at the same moment queue on that one
        row, so the second counts after the first has committed, sees the agency full and undoes its claim. That wait is only ever
        between two claims for the same agency, and lasts one short transaction.
        """
        for _ in range(8):
            try:
                async with self._cursor() as cur:
                    await cur.execute(self.TURNS, self._p(limit=self.per_org_limit, orgs=8))
                    job = None
                    for turn in await cur.fetchall():
                        await cur.execute(self.CLAIM, self._p(org=turn["org_id"], worker=worker, lease=self.lease))
                        if (job := await cur.fetchone()) is not None:
                            break
                    if job is None:
                        return None
                    await cur.execute(
                        "insert into org_last_served (org_id, served_at) values (%(org)s, clock_timestamp()) "
                        "on conflict (org_id) do update set served_at = excluded.served_at", {"org": job["org_id"]})
                    await cur.execute("select count(*) as n from jobs where org_id = %(org)s and status = 'running'", {"org": job["org_id"]})
                    if int((await cur.fetchone())["n"]) > self.per_org_limit:
                        raise _OverLimit
                    return job
            except _OverLimit:
                continue
        return None

    async def heartbeat(self, job_id: int, worker: str) -> bool:
        """Extend the lease. False means the job is no longer this worker's (its lease ran out and it was given to another)."""
        row = await self._one(
            f"update jobs set locked_until = {NOW} + %(lease)s where id = %(id)s and locked_by = %(worker)s and status = 'running' returning id",
            self._p(id=job_id, worker=worker, lease=self.lease))
        return row is not None

    async def complete(self, job_id: int, worker: str, result: dict | None = None) -> bool:
        row = await self._one(
            f"update jobs set status = 'done', result = %(result)s, finished_at = {NOW}, locked_by = null, locked_until = null, last_error = null "
            "where id = %(id)s and locked_by = %(worker)s and status = 'running' returning id",
            self._p(id=job_id, worker=worker, result=Jsonb(_jsonable(result or {}))))
        return row is not None

    def describe(self, exc: BaseException) -> str:
        return redact(f"{type(exc).__name__}: {exc}", self._secrets)[:ERROR_LIMIT]

    async def fail(self, job_id: int, worker: str, error: str | BaseException, *, permanent: bool = False) -> str | None:
        """Record a failure. The job is retried after a back-off ('failed'), or is 'dead' once it has used all its attempts (or at once
        when `permanent`). Returns the new status, or None when the job was no longer this worker's."""
        text = self.describe(error) if isinstance(error, BaseException) else redact(error, self._secrets)[:ERROR_LIMIT]
        async with self._cursor() as cur:
            await cur.execute("select attempts, max_attempts from jobs where id = %(id)s and locked_by = %(worker)s and status = 'running' for update",
                              {"id": job_id, "worker": worker})
            row = await cur.fetchone()
            if row is None:
                return None
            dead = permanent or row["attempts"] >= row["max_attempts"]
            delay = timedelta(seconds=0 if dead else backoff_seconds(row["attempts"], base=self._backoff[0], cap=self._backoff[1], rng=self._rng))
            await cur.execute(
                f"update jobs set status = %(status)s, run_after = {NOW} + %(delay)s, last_error = %(error)s, locked_by = null, locked_until = null, "
                f"finished_at = case when %(status)s::text = 'dead' then {NOW} end where id = %(id)s",
                self._p(id=job_id, status="dead" if dead else "failed", delay=delay, error=text))
            return "dead" if dead else "failed"

    async def reap_expired(self) -> list[dict]:
        """Running jobs whose lease ran out (their worker died or hung) go back to the queue, or are dead if that was their last attempt."""
        async with self._cursor() as cur:
            await cur.execute(
                "update jobs set status = case when attempts >= max_attempts then 'dead' else 'queued' end, "
                f"finished_at = case when attempts >= max_attempts then {NOW} end, "
                "last_error = 'the lease expired: the worker stopped or hung', locked_by = null, locked_until = null "
                f"where status = 'running' and locked_until < {NOW} returning id, org_id, kind, status", self._p())
            return list(await cur.fetchall())

    # ---- looking and steering ----

    async def get(self, job_id: int) -> dict | None:
        return await self._one("select * from jobs where id = %(id)s", {"id": job_id})

    async def retry(self, job_id: int) -> dict | None:
        """An operator's retry of a dead or failed job: queued now, with all its attempts back. None if it is not dead or failed."""
        return await self._one(
            f"update jobs set status = 'queued', attempts = 0, run_after = {NOW}, locked_by = null, locked_until = null, finished_at = null "
            "where id = %(id)s and status in ('dead', 'failed') returning *", self._p(id=job_id))

    async def list(self, *, org_id: UUID | str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        async with self._cursor() as cur:
            await cur.execute(
                "select j.id, j.org_id, o.slug as organization, j.kind, j.status, j.priority, j.attempts, j.max_attempts, j.run_after, "
                "j.last_error, j.result, j.created_at, j.started_at, j.finished_at from jobs j join organizations o on o.id = j.org_id "
                "where (%(org)s::uuid is null or j.org_id = %(org)s::uuid) and (%(status)s::text is null or j.status = %(status)s::text) "
                "order by j.id desc limit %(limit)s",
                {"org": None if org_id is None else str(org_id), "status": status, "limit": max(1, min(limit, 200))})
            return list(await cur.fetchall())

    async def counts(self, *, org_id: UUID | str | None = None) -> dict[str, int]:
        async with self._cursor() as cur:
            await cur.execute("select status, count(*) as n from jobs where (%(org)s::uuid is null or org_id = %(org)s::uuid) group by status",
                              {"org": None if org_id is None else str(org_id)})
            found = {r["status"]: int(r["n"]) for r in await cur.fetchall()}
        return {s: found.get(s, 0) for s in STATUSES}


def _jsonable(value: Any) -> Any:
    """Job results go into jsonb: dates and ids become text."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def queue_of(state: Any) -> JobQueue:
    """The app's queue. An app built without its start-up (the tests) gets one on its repository's connections."""
    if getattr(state, "jobs", None) is None:
        state.jobs = JobQueue(state.repo.pool)
    return state.jobs


# ---------------- what a job is ----------------


@dataclass(frozen=True)
class JobContext:
    job: dict
    state: Any  # the application's state: its services (proactive, projects, runtime...)

    @property
    def payload(self) -> dict:
        return dict(self.job.get("payload") or {})

    @property
    def org_id(self) -> UUID:
        return UUID(str(self.job["org_id"]))


JobFn = Callable[[JobContext], Awaitable[dict | None]]
REGISTRY: dict[str, JobFn] = {}


def job(kind: str) -> Callable[[JobFn], JobFn]:
    """Register a function as the code for a kind of job. It must be safe to run twice."""

    def register(fn: JobFn) -> JobFn:
        if kind in REGISTRY:
            raise ValueError(f"job kind {kind!r} is registered twice")
        REGISTRY[kind] = fn
        return fn

    return register


def scope_for(state: Any) -> Callable[[UUID], Any]:
    """How a job enters its agency: its database rows, settings and channels when the app has a runtime, else just its rows."""
    runtime = getattr(state, "runtime", None)
    if runtime is not None:
        return runtime.scope

    @asynccontextmanager
    async def rows_only(org: UUID) -> AsyncIterator[Any]:
        with use_tenant(org) as tenant:
            yield tenant

    return rows_only


class UnknownJob(Exception):
    pass


class JobWorker:
    """N asyncio tasks that claim and run jobs until stopped. One process may run one; several processes may each run one."""

    def __init__(
        self, queue: JobQueue, state: Any, *, concurrency: int = 2, poll_interval: float = 2.0, reap_interval: float = 15.0,
        registry: dict[str, JobFn] | None = None, name: str | None = None,
    ):
        self.queue, self.state = queue, state
        self.concurrency, self.poll_interval, self.reap_interval = concurrency, poll_interval, reap_interval
        self.registry = REGISTRY if registry is None else registry
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    # ---- running one job ----

    async def run_one(self, worker: str | None = None) -> dict | None:
        """Claim one job and run it to the end. Returns the claimed row, or None when nothing was due."""
        wid = worker or f"{self.name}:0"
        claimed = await self.queue.claim(wid)
        if claimed is not None:
            await self.execute(claimed, wid)
        return claimed

    async def run_until_idle(self, max_jobs: int = 10_000) -> int:
        """Run due jobs one at a time until none is left; for tests and one-off scripts. Returns how many ran."""
        ran = 0
        while ran < max_jobs and await self.run_one() is not None:
            ran += 1
        return ran

    async def execute(self, claimed: dict, worker: str) -> None:
        job_id, kind = int(claimed["id"]), claimed["kind"]
        fn = self.registry.get(kind)
        if fn is None:
            await self.queue.fail(job_id, worker, f"no code is registered for job kind {kind!r}", permanent=True)
            return
        beat = asyncio.create_task(self._heartbeat(job_id, worker))
        try:
            async with scope_for(self.state)(UUID(str(claimed["org_id"]))):  # the agency comes from the row, never from the payload
                result = await fn(JobContext(claimed, self.state))
        except asyncio.CancelledError:
            raise  # the worker is stopping or died: the lease runs out and the reaper puts the job back
        except Exception as exc:
            log.warning("job %s (%s) failed: %s", job_id, kind, self.queue.describe(exc))
            await self.queue.fail(job_id, worker, exc)
        else:
            if not await self.queue.complete(job_id, worker, result):
                log.warning("job %s (%s) finished after its lease was lost; its effects are deduplicated, the record is left to the new owner", job_id, kind)
        finally:
            beat.cancel()

    async def _heartbeat(self, job_id: int, worker: str) -> None:
        every = max(0.05, self.queue.lease.total_seconds() / 3)
        while True:
            await asyncio.sleep(every)
            try:
                if not await self.queue.heartbeat(job_id, worker):
                    return
            except Exception:
                log.exception("lease of job %s could not be extended", job_id)

    # ---- the loop ----

    async def start(self) -> None:
        self._stopping.clear()
        self._tasks = [asyncio.create_task(self._loop(i), name=f"job-worker-{i}") for i in range(self.concurrency)]
        log.info("job worker %s started with %d tasks", self.name, self.concurrency)

    async def stop(self, grace: float = 10.0) -> None:
        """Let running jobs finish for up to `grace` seconds, then cancel them (their leases run out and they are retried)."""
        self._stopping.set()
        self.queue.wake.set()
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self, n: int) -> None:
        wid = f"{self.name}:{n}"
        loop = asyncio.get_running_loop()
        next_reap = loop.time()
        while not self._stopping.is_set():
            try:
                if n == 0 and loop.time() >= next_reap:
                    next_reap = loop.time() + self.reap_interval
                    for row in await self.queue.reap_expired():
                        log.warning("job %s (%s) lost its worker; now %s", row["id"], row["kind"], row["status"])
                claimed = await self.queue.claim(wid)
            except Exception:
                log.exception("the job queue could not be read; trying again shortly")
                claimed = None
            if claimed is None:
                try:
                    await asyncio.wait_for(self.queue.wake.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    pass
                self.queue.wake.clear()
                continue
            await self.execute(claimed, wid)


# ---------------- the scheduled jobs ----------------
#
# Each takes the app's state and `dry_run`. The cron endpoints run them inline for a preview (`dry_run`) and queue them otherwise.
# All are safe to run twice: every message they send is claimed once per recipient first (`claim_event`).


async def daily_run(state: Any, dry_run: bool = False) -> dict:
    result = await state.proactive.run_all(dry_run)
    result["jobs"].append(await state.projects.run_promotion_ads(dry_run))  # developers' current offers, advertised once each
    for key in ("sent", "skipped", "failed"):
        result[key] = sum(j[key] for j in result["jobs"])
    return result


async def reverse_rag(state: Any, dry_run: bool = False) -> dict:
    return await state.proactive.reverse_rag_alerts(dry_run)


async def tour_reminders(state: Any, dry_run: bool = False) -> dict:
    return await state.proactive.tour_reminders(dry_run)


async def milestone_reminders(state: Any, dry_run: bool = False) -> dict:
    return await state.proactive.milestone_reminders(dry_run)


async def commission_overdue(state: Any, dry_run: bool = False) -> dict:
    return await state.proactive.commission_overdue(dry_run)


async def promotion_ads(state: Any, dry_run: bool = False) -> dict:
    return await state.projects.run_promotion_ads(dry_run)


async def audit_export(state: Any, dry_run: bool = False) -> dict:
    from app.auditchain import audit_export as run

    return await run(state, dry_run)


SCHEDULED: dict[str, tuple[Callable[[Any, bool], Awaitable[dict]], str]] = {
    # kind: (the work, how often it is meant to run: one queued job per agency per day or per hour)
    "proactive.daily": (daily_run, "day"),
    "proactive.reverse_rag": (reverse_rag, "hour"),
    "proactive.tour_reminders": (tour_reminders, "hour"),
    "proactive.milestone_reminders": (milestone_reminders, "hour"),
    "projects.promotion_ads": (promotion_ads, "hour"),
    "audit.export": (audit_export, "day"),
    "commission.overdue": (commission_overdue, "day"),
}


def _scheduled(work: Callable[[Any, bool], Awaitable[dict]]) -> JobFn:
    async def run(ctx: JobContext) -> dict:
        return await work(ctx.state, False)

    return run


for _kind, (_work, _) in SCHEDULED.items():
    job(_kind)(_scheduled(_work))


def period_key(kind: str, trigger: str, at: datetime | None = None) -> str:
    """The dedupe key for a scheduled job: the cron's own calls queue it once per period (a repeated or overlapping cron call is a
    no-op), while a person pressing the console's button queues it once per minute (a double click is still one job)."""
    at = (at or datetime.now(UTC)).astimezone(UTC)
    if trigger != "cron":
        return f"{trigger}:{at:%Y-%m-%dT%H:%M}"
    return f"cron:{at:%Y-%m-%d}" if SCHEDULED[kind][1] == "day" else f"cron:{at:%Y-%m-%dT%H}"
