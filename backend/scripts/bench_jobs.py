"""Job-queue fairness benchmark: does one busy agency hold up the others?

One "noisy" agency queues many slow jobs first; nine "quiet" agencies then queue a few fast ones each. Real workers (asyncio tasks
on their own pooled connections) run them, and the script reports when the quiet agencies' work was done:

- `fair`: the queue as built (app/jobs.py): agencies take turns and one agency holds at most `--per-org` workers.
- `fifo`: the same workers taking jobs strictly in the order they were queued, which is what a plain queue does.
- `inline`: the old cron path (`run_for_orgs`): one agency after another inside the request; computed from the job durations.

A last run times the claim itself with jobs that do nothing (claims per second).

    python -m scripts.bench_jobs --noisy 1000 --quiet-orgs 9 --quiet-jobs 10 --workers 4 --per-org 2

Uses the throw-away database `proppulse_bench` (see scripts/bench_tenants.py), migrated but not seeded. Prints a Markdown table.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from types import SimpleNamespace
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

import app  # noqa: F401  (picks the Windows-compatible event loop)
from app.jobs import NOW, JobContext, JobQueue, JobWorker


class FifoQueue(JobQueue):
    """The comparison: first queued, first run, no turns between agencies and no per-agency limit."""

    async def claim(self, worker: str) -> dict | None:
        async with self._cursor() as cur:
            await cur.execute(
                f"update jobs set status = 'running', locked_by = %(worker)s, locked_until = {NOW} + %(lease)s, attempts = attempts + 1, "
                f"started_at = {NOW} where id = (select id from jobs where status in ('queued', 'failed') and run_after <= {NOW} "
                "order by id limit 1 for update skip locked) returning *", self._p(worker=worker, lease=self.lease))
            return await cur.fetchone()  # type: ignore[no-any-return]


def fresh_db() -> str:
    from scripts.bench_tenants import bench_url
    from scripts.migrate import migrate

    url = bench_url()
    migrate(url)
    return url


def make_orgs(url: str, quiet: int) -> tuple[str, list[str]]:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("delete from organizations where slug like 'jobbench-%%'")
        noisy = conn.execute("insert into organizations (slug, name, plan) values ('jobbench-noisy', 'Noisy', 'business') returning id::text").fetchone()[0]  # type: ignore[index]
        quiet_ids = [conn.execute("insert into organizations (slug, name, plan) values (%s, %s, 'business') returning id::text", (f"jobbench-quiet-{i}", f"Quiet {i}")).fetchone()[0]  # type: ignore[index]
                     for i in range(quiet)]
    return noisy, quiet_ids


async def scenario(url: str, queue_cls: type[JobQueue], *, noisy_jobs: int, quiet_orgs: int, quiet_jobs: int, slow: float, fast: float,
                   workers: int, per_org: int) -> dict[str, Any]:
    noisy, quiet = make_orgs(url, quiet_orgs)
    pool = AsyncConnectionPool(url, min_size=2, max_size=workers + 2, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None})
    await pool.open(wait=True)
    try:
        queue = queue_cls(pool, per_org_limit=per_org if queue_cls is JobQueue else 10_000)
        for _ in range(noisy_jobs):
            await queue.enqueue(noisy, "bench.slow")
        start = time.perf_counter()
        for org in quiet:
            for _ in range(quiet_jobs):
                await queue.enqueue(org, "bench.fast")
        finished: dict[str, list[float]] = {"noisy": [], "quiet": []}
        total = noisy_jobs + quiet_orgs * quiet_jobs
        all_done = asyncio.Event()

        def mark(group: str) -> None:
            finished[group].append(time.perf_counter() - start)
            if len(finished["noisy"]) + len(finished["quiet"]) >= total:
                all_done.set()

        async def slow_job(ctx: JobContext) -> dict:
            await asyncio.sleep(slow)
            mark("noisy")
            return {}

        async def fast_job(ctx: JobContext) -> dict:
            await asyncio.sleep(fast)
            mark("quiet")
            return {}

        worker = JobWorker(queue, SimpleNamespace(), concurrency=workers, poll_interval=0.05,
                           registry={"bench.slow": slow_job, "bench.fast": fast_job})
        await worker.start()
        await all_done.wait()
        await worker.stop()
        q = sorted(finished["quiet"])
        return {"quiet_p50": statistics.median(q), "quiet_p95": q[int(0.95 * (len(q) - 1))], "quiet_done": q[-1], "noisy_done": max(finished["noisy"])}
    finally:
        await pool.close()


async def claim_rate(url: str, jobs: int, workers: int) -> float:
    noisy, quiet = make_orgs(url, 19)
    pool = AsyncConnectionPool(url, min_size=2, max_size=workers + 2, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None})
    await pool.open(wait=True)
    try:
        queue = JobQueue(pool, per_org_limit=2)
        orgs = [noisy, *quiet]
        for i in range(jobs):
            await queue.enqueue(orgs[i % len(orgs)], "bench.noop")
        done = 0
        all_done = asyncio.Event()

        async def noop(ctx: JobContext) -> dict:
            nonlocal done
            done += 1
            if done >= jobs:
                all_done.set()
            return {}

        worker = JobWorker(queue, SimpleNamespace(), concurrency=workers, poll_interval=0.05, registry={"bench.noop": noop})
        start = time.perf_counter()
        await worker.start()
        await all_done.wait()
        elapsed = time.perf_counter() - start
        await worker.stop()
        return jobs / elapsed
    finally:
        await pool.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--noisy", type=int, default=1000, help="slow jobs queued first by the busy agency")
    ap.add_argument("--quiet-orgs", type=int, default=9)
    ap.add_argument("--quiet-jobs", type=int, default=10, help="fast jobs per quiet agency")
    ap.add_argument("--slow-ms", type=float, default=20)
    ap.add_argument("--fast-ms", type=float, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per-org", type=int, default=2)
    ap.add_argument("--claims", type=int, default=2000, help="no-op jobs for the claim-rate run")
    args = ap.parse_args()
    url = fresh_db()
    common = dict(noisy_jobs=args.noisy, quiet_orgs=args.quiet_orgs, quiet_jobs=args.quiet_jobs, slow=args.slow_ms / 1000, fast=args.fast_ms / 1000,
                  workers=args.workers, per_org=args.per_org)
    rows = []
    for name, cls, noisy in (("fair", JobQueue, args.noisy), ("fair, 3x noisy", JobQueue, args.noisy * 3), ("fifo", FifoQueue, args.noisy)):
        r = await scenario(url, cls, **{**common, "noisy_jobs": noisy})  # type: ignore[arg-type]
        rows.append((name, noisy, r))
        print(f"  {name}: done", flush=True)
    inline_quiet = args.noisy * args.slow_ms / 1000 + args.quiet_orgs * args.quiet_jobs * args.fast_ms / 1000  # noisy agency first, one after another
    rate = await claim_rate(url, args.claims, args.workers)

    print(f"\n{args.workers} workers, at most {args.per_org} per agency; noisy jobs {args.slow_ms:g} ms each, quiet jobs {args.fast_ms:g} ms; "
          f"{args.quiet_orgs} quiet agencies x {args.quiet_jobs} jobs, queued after the noisy agency's.\n")
    print("| Queue | Noisy jobs | Quiet jobs p50 | Quiet jobs p95 | Last quiet job done | Noisy agency done |")
    print("|---|---:|---:|---:|---:|---:|")
    for name, noisy, r in rows:
        print(f"| {name} | {noisy} | {r['quiet_p50']:.2f} s | {r['quiet_p95']:.2f} s | {r['quiet_done']:.2f} s | {r['noisy_done']:.2f} s |")
    print(f"| inline (before, computed) | {args.noisy} | - | - | {inline_quiet:.2f} s | {args.noisy * args.slow_ms / 1000:.2f} s |")
    print(f"\nClaim rate: {rate:.0f} jobs/s ({args.claims} no-op jobs, 20 agencies, {args.workers} workers).")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("delete from organizations where slug like 'jobbench-%%'")


if __name__ == "__main__":
    asyncio.run(main())
