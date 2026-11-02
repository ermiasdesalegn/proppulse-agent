# ADR 0001: One shared database with row-level security, not a schema or database per agency

- Status: accepted (2026-11-02)
- Deciders: project owner
- Related: [THREAT_MODEL.md](../THREAT_MODEL.md), [BENCHMARKS.md](../BENCHMARKS.md), `backend/migrations/020` to `024`, `backend/app/tenancy.py`

## Context

PropPulse started as one broker's tool. To serve many agencies, each agency's buyers, chats, tours, deals and channel credentials must be invisible to every other agency, while the scraped market (developers, projects, listings, prices) stays common and is crawled once.

Constraints that shape the choice:

- The hosted database is Supabase's free tier: one Postgres database, small connection limit, a transaction-mode pooler. Creating databases or large numbers of schemas on demand is not available there.
- One person maintains it. Migrations must stay simple to apply and test.
- The tests run on an embedded Postgres in seconds; the isolation guarantees have to be testable the same way.
- The main risk is not an attacker but a mistake: a query written without the agency filter.

## Options

| | Shared tables + `org_id` + RLS | Schema per agency | Database per agency |
|---|---|---|---|
| Isolation strength | Enforced by the database per row; a forgotten filter fails closed | By `search_path`; a wrong path reads another agency | Strongest; separate credentials possible |
| Shared market data | Natural (same tables) | Needs cross-schema reads or copies | Needs copies or a federation layer |
| Migrations | Once | Once per schema, must be looped and kept in step | Once per database |
| Pooler / connection limits | One pool | One pool, but `search_path` must be set per transaction | One pool per database: breaks the free tier at tens of agencies |
| Onboarding an agency | Insert a row | Create a schema and run migrations | Provision a database |
| Per-agency backup or export | Query by `org_id` | Dump a schema | Dump a database |
| Cost of isolation | Measured: about 0.1 ms per request ([BENCHMARKS.md](../BENCHMARKS.md)) | Not measured | Not measured |

## Decision

One database, one set of tables. Every private table carries `org_id`. The application runs each request's transaction as a restricted role (`app_user`) and labels it with the agency (`set_config('app.org_id', ..., true)`); policies on every private table compare `org_id` to that label. Composite foreign keys `(org_id, id)` stop a row from pointing at another agency's record. Shared market tables are readable by all and written by the platform's crawlers. Credentials are encrypted per agency under one master key (`app/vault.py`).

The label is set per transaction (`set local`), so it works through a transaction-mode pooler and cannot leak to the next user of a connection.

## Consequences

Good:

- A query without a filter returns nothing instead of everything; the test suite proves it for every table.
- One migration path, one pool, one backup; onboarding an agency is an insert.
- The market data is shared without copies.
- Measured overhead is small, and the vector-search behaviour is understood (see below).

Costs and risks accepted:

- **Isolation is only as strong as the role setup.** The table owner bypasses the policies, so the owner's connection string is as sensitive as the master key. Code that can run arbitrary SQL could set its own label; the design defends against bugs, not against SQL injection ([THREAT_MODEL.md](../THREAT_MODEL.md), T13).
- **Noisy neighbours share resources.** One busy agency can slow the rest. Mitigation later: per-agency quotas, and partitioning by organization if a single agency grows large.
- **Vector indexes and RLS interact badly.** An HNSW index over private vectors returned 9 of 50 requested rows because other agencies' neighbours fill the candidate list and are then filtered out. At 20,000 buyers an exact scan is faster and correct, so there is no vector index on private data today. Re-measure on pgvector 0.8 (iterative scans) or partition before adding one.
- **Shared tables are read-only for `app_user`** (migration 025). The writers (crawlers, project and listing edits) run through `Repository._shared()` as the platform, so the shared market data cannot be rewritten by a bug in tenant code; the cost is that those paths must be listed in one place and checked when a new shared table or writer is added (`tests/test_tenancy_isolation.py` fails if a tenant can write one).
- Per-agency data residency or a customer who contractually requires its own database cannot be met by this design; that customer would be a separate deployment.

## When to reconsider

- A customer needs physical separation (regulation, contract), or a single agency's data outgrows what shares well: move that agency to its own database, keeping the same schema so the application changes only in how it picks a connection.
- The number of agencies reaches the point where policy evaluation or table sizes show in the benchmark; re-run `scripts/bench_tenants.py` with larger `--orgs` and `--buyers`.
