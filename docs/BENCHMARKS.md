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
