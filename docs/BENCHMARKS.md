# Benchmark: 50 agencies in one database

Question: does keeping every agency in the same tables, separated by row-level security (RLS), cost noticeable time per request, and does the vector search still behave when each agency may only see its own buyers? Decision this feeds: [ADR 0001](adr/0001-shared-database-with-row-level-security.md).

## Setup

- `python -m scripts.bench_tenants` (from `backend/`). It builds a throw-away database next to the test one, applies all migrations, then adds **50 agencies × 400 buyers × 10 chat messages = 20,000 buyers and 200,000 messages**, each buyer with a 768-dimension preference vector.
- Each measurement is one transaction in a random agency, as a request is: set the agency label, switch to the restricted `app_user` role, run the query, roll back. 200 timed runs after 10 warm-up runs; p50 and p95 in milliseconds, wall clock including the two setup statements.
- Baseline: the table owner (no RLS) with an explicit `where org_id = ...`, which is what hand-written tenant filtering would do.
- Machine: a development laptop, PostgreSQL 16.2 with pgvector 0.6.2 (the embedded server the tests use), client on the same machine. **Absolute numbers will differ on a hosted database (add the network round trip); the comparisons are what matter.** Vectors are random, so similarity values mean nothing here; only timing and the number of rows returned do.

## Results (one run, 2026-11-02)

| Query (20,000 buyers in 50 agencies) | p50 ms | p95 ms | rows |
|---|---:|---:|---:|
| chat history, 20 newest (owner, explicit `org_id`) | 0.37 | 0.50 | 10 |
| chat history, 20 newest (`app_user`, RLS) | 0.49 | 0.63 | 10 |
| buyer by Telegram id (owner, explicit `org_id`) | 0.47 | 0.64 | 1 |
| buyer by Telegram id (`app_user`, RLS) | 0.57 | 0.82 | 1 |
| buyers matching a unit, top 50 (`match_buyers_for_property`, RLS, no vector index) | 2.15 | 2.61 | 50 |
| 50 nearest buyers (RLS, exact scan) | 2.84 | 6.55 | 50 |
| 50 nearest buyers (RLS, HNSW index, default `ef_search`) | 1.61 | 2.02 | **9** |
| 50 nearest buyers (RLS, HNSW index, `ef_search = 1000`) | 8.77 | 10.41 | 50 |

(Chat history returns 10 rows because each synthetic buyer has 10 messages.)

## What it shows

1. **Isolation costs about 0.1 ms per request.** RLS plus the role switch adds 0.10 to 0.12 ms at p50 on lookups that take 0.4 to 0.5 ms; p95 stays under 1 ms. The existing indexes (`(buyer_id, id)`, `(org_id, telegram_id)`) are used, so the policy's `org_id` condition does not cause a scan.
2. **A vector index on private data silently loses results.** With an HNSW index the database finds the nearest 40-odd vectors *across all agencies*, then RLS removes other agencies' rows. The query asked for 50 and returned **9**, with no error. Raising `ef_search` to 1000 returns all 50 but takes 8.8 ms, slower than not having the index. (pgvector 0.8's iterative scans address this; they need a newer extension than the local one, so that variant was not measured here. Supabase runs 0.8 or later; re-run the script there before relying on it.)
3. **At this size the exact scan wins.** Scanning 20,000 vectors takes under 3 ms and is always exact. The production schema has no vector index on `buyers_crm.preference_embedding`, and this benchmark is the reason not to add one yet.
4. **Forward search is unaffected.** Listings (`properties`) are shared market data without RLS filtering by agency, so the property search is the same query as before multi-tenancy and is not in the table.

## When to revisit

- The exact scan grows linearly: at roughly 500,000 buyers expect 70 ms or more. Then use pgvector 0.8 iterative scans (`hnsw.iterative_scan = relaxed_order`) and **check the row count returned**, or partition `buyers_crm` by organization so each agency has its own small index.
- The reverse match runs from the proactive jobs, not from a buyer's chat message, so even 70 ms there is not on a buyer's critical path.

## Not measured

- Concurrency. One connection ran the queries one after another; pool contention and lock behaviour under parallel load are untested.
- Writes (inserting messages, updating buyers) under RLS.
- A hosted database: network latency and the connection pooler (transaction mode, which is why the label is set with `set_config(..., true)` inside each transaction) were not in the loop.

---

# Benchmark: a job queue that is fair between agencies

`python -m scripts.bench_jobs` (backend), on the embedded Postgres, database `proppulse_bench` (migrated, not seeded). One run, 2026-11-02, Windows laptop.

## Setup

- One **noisy** agency queues 1,000 slow jobs (20 ms each) **first**; then nine **quiet** agencies queue 10 fast jobs (2 ms) each.
- Four workers (asyncio tasks in one process, each claim and finish on pooled connections), at most two running jobs per agency.
- `fair` is the queue as built. `fifo` is the same workers taking jobs in the order they were queued, as a plain queue does. `inline` is the cron path before this change (`run_for_orgs`: one agency after another inside the request), computed from the job durations with the noisy agency first.
- Times are from when the quiet agencies started queueing.

## Results

| Queue | Noisy jobs | Quiet jobs p50 | Quiet jobs p95 | Last quiet job done | Noisy agency done |
|---|---:|---:|---:|---:|---:|
| fair | 1000 | 0.24 s | 0.34 s | 0.36 s | 19.45 s |
| fair, 3x noisy | 3000 | 0.22 s | 0.30 s | 0.31 s | 53.27 s |
| fifo | 1000 | 10.07 s | 10.11 s | 10.12 s | 9.96 s |
| inline (before, computed) | 1000 | - | - | 20.18 s | 20.00 s |

Claim rate: 594 jobs/s (2,000 jobs that do nothing, 20 agencies, 4 workers).

## What it shows

1. **The quiet agencies no longer wait for the noisy one.** Their last job is done in about a third of a second, against 10 s with a plain queue and 20 s with the old inline loop, and the time does not grow when the noisy agency queues three times as much (0.36 s with 1,000 noisy jobs, 0.31 s with 3,000).
2. **The price is paid by the noisy agency.** It is held to two of the four workers, so its 1,000 jobs take about twice as long as with a plain queue, even after the quiet agencies are finished and two workers sit idle. That is what `JOB_PER_ORG_LIMIT` is for; with few agencies it can be raised.
3. **A claim must not look at every queued job.** The first version chose with one statement that sorted every due row and counted each one's agency: at 1,000 queued jobs a claim took about 20 ms, and the quiet agencies' wait grew with the noisy agency's backlog (0.52 s, then 1.52 s at 3,000). The claim now finds the agencies with work by skipping through an index one agency at a time, then takes that agency's next job from its own index, so its cost follows the number of waiting agencies, not jobs.
4. Each 20 ms job costs about 39 ms of a worker's time here: Windows' timer granularity rounds the sleeps up, and each job is two short transactions (claim, finish). The comparison between the rows is the point, not the absolute numbers.

## Not measured

- Several processes or machines (the concurrency test runs four workers with their own pools in one process; the claim's correctness does not depend on that, its throughput might).
- A hosted database: every claim is a few round trips, so network latency would lower the claim rate.
- Real jobs. The proactive jobs call Telegram and the calendar; their time is the work itself, not the queue.

---

# Benchmark: property search with per-agency prices

`python -m scripts.bench_tenants` (backend), the same run as the first benchmark, with the catalogue grown to 3,015 units. One run, 2026-11-02.

The search is the buyer-facing one (`match_properties_hybrid`: nearest by meaning, within budget and bedrooms), as `app_user` with
row-level security. "Before 028" is the same query on the shared table; the other two go through `agency_properties`, first with no agency
prices, then with each of the 50 agencies pricing about a third of the units (50,159 rows in `agency_prices`).

| Search | p50 ms | p95 ms |
|---|---:|---:|
| shared prices, before 028 | 1.24 | 1.69 |
| agency view, no agency prices | 1.34 | 1.65 |
| agency view, 50,159 agency prices | 1.42 | 1.71 |

## What it shows

1. **The agency's price costs about 0.1 to 0.2 ms at p50 and nothing measurable at p95.** The view adds one primary-key lookup per
   candidate unit (`org_id, target_kind, target_id`).
2. **One index made it worse.** The first version also had an index on `(target_kind, target_id)`. The planner preferred it, read every
   agency's price for each unit (12 here) and filtered them: p95 2.83 ms, 46% over the shared-price search. Without it the primary key is
   used and the difference disappears. Nothing needs that index today; if something does later, put `org_id` last in it.
3. Each run is a fresh transaction, so the numbers include planning, which the view makes slightly larger.
