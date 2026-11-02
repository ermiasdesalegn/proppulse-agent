"""Tenant-isolation benchmark: does row-level security stay fast with many agencies in one database?

Builds a throw-away database (`proppulse_bench`, on the same embedded Postgres the tests use, or on BENCH_DATABASE_URL), fills it
with synthetic agencies, buyers and chat messages, then times the queries a request makes, as the restricted `app_user` role with
row-level security on and, for comparison, as the table owner with an explicit `org_id` filter.

    python -m scripts.bench_tenants --orgs 50 --buyers 400 --messages 10 --runs 200

Prints a Markdown table (copy it into docs/BENCHMARKS.md). Never run against a real database: it inserts thousands of rows.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import time
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parents[1]
DB_NAME = "proppulse_bench"


def bench_url() -> str:
    if explicit := os.environ.get("BENCH_DATABASE_URL"):
        return explicit
    import pgserver

    server = pgserver.get_server(BACKEND / ".pgdata", cleanup_mode="stop")
    admin = server.get_uri()
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{DB_NAME}" with (force)')
        conn.execute(f'create database "{DB_NAME}"')
    url = admin.rsplit("/", 1)[0] + f"/{DB_NAME}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("create extension if not exists vector")
    bench_url.server = server  # type: ignore[attr-defined]  # keep the embedded server alive for the run
    return url


def build(url: str, orgs: int, buyers: int, messages: int) -> list[str]:
    from scripts.migrate import migrate
    from scripts.seed import main as seed
    from tests.localdb import EMBEDDINGS

    migrate(url)
    seed(url=url, embeddings_file=str(EMBEDDINGS))
    with psycopg.connect(url, autocommit=True) as conn:
        # 400 random unit-ish vectors, reused with a small per-row twist, so building 20k embeddings takes seconds, not minutes.
        conn.execute("create temp table base_vectors as select g as n, (select array_agg(random() - 0.5) "
                     "from generate_series(1, 768) where g > 0)::vector(768) as v from generate_series(1, 400) g")
        conn.execute("insert into organizations (slug, name) select 'bench-' || g, 'Bench agency ' || g from generate_series(1, %s) g", (orgs,))
        conn.execute(
            "insert into buyers_crm (org_id, thread_id, telegram_id, full_name, max_budget_etb, min_bedrooms, preferred_sub_cities, "
            "deal_stage, preference_embedding) "
            "select o.id, 'tg:' || g, 1000000 + g, 'Buyer ' || g, 3000000 + (g %% 40) * 500000, 1 + g %% 4, '{}', "
            "(array['NEW','QUALIFIED','QUALIFIED','SITE_VISIT_BOOKED'])[1 + g %% 4], b.v "
            "from organizations o cross join generate_series(1, %s) g "
            "join base_vectors b on b.n = 1 + ((g * 7 + length(o.slug)) %% 400) where o.slug like 'bench-%%'", (buyers,))
        conn.execute(
            "insert into chat_messages (org_id, buyer_id, role, content) "
            "select b.org_id, b.id, (array['buyer','agent'])[1 + m %% 2], 'message ' || m || ' ' || repeat('x', 80) "
            "from buyers_crm b cross join generate_series(1, %s) m where b.full_name like 'Buyer %%'", (messages,))
        conn.execute("analyze")
        return [r[0] for r in conn.execute("select id::text from organizations where slug like 'bench-%' order by slug")]


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def measure(url: str, org_ids: list[str], runs: int, make_query, *, role: str | None, setup: tuple[str, ...] = ()) -> tuple[float, float, int]:
    """(p50 ms, p95 ms, rows returned by the last run). Each run is its own transaction, as a request is, in a random agency."""
    rng = random.Random(7)
    times: list[float] = []
    rows = 0
    with psycopg.connect(url) as conn:
        for i in range(runs + 10):  # the first ten warm the caches and are not counted
            org = rng.choice(org_ids)
            sql, params = make_query(conn, org)
            t0 = time.perf_counter()
            conn.execute("select set_config('app.org_id', %s, true)", (org,))
            if role:
                conn.execute(f"set local role {role}")
            for stmt in setup:
                conn.execute(stmt)
            rows = len(conn.execute(sql, params).fetchall())
            elapsed = (time.perf_counter() - t0) * 1000
            conn.rollback()
            if i >= 10:
                times.append(elapsed)
    return statistics.median(times), pct(times, 0.95), rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orgs", type=int, default=50)
    ap.add_argument("--buyers", type=int, default=400, help="buyers per agency")
    ap.add_argument("--messages", type=int, default=10, help="chat messages per buyer")
    ap.add_argument("--runs", type=int, default=200)
    args = ap.parse_args()

    url = bench_url()
    t0 = time.perf_counter()
    org_ids = build(url, args.orgs, args.buyers, args.messages)
    print(f"built {args.orgs} agencies x {args.buyers} buyers x {args.messages} messages in {time.perf_counter() - t0:.0f} s\n")

    with psycopg.connect(url) as conn:
        sample: dict[str, tuple[str, int, str]] = {}
        for org in org_ids:
            buyer = conn.execute("select id::text, telegram_id from buyers_crm where org_id = %s and full_name = 'Buyer 5'", (org,)).fetchone()
            sample[org] = (buyer[0], buyer[1], org)
        prop = conn.execute("select id::text, embedding::text from properties limit 1").fetchone()
        total = conn.execute("select count(*) from buyers_crm").fetchone()[0]
        conn.rollback()

    def chat(_c, org):
        return "select role, content from chat_messages where buyer_id = %s order by id desc limit 20", (sample[org][0],)

    def chat_explicit(_c, org):
        return ("select role, content from chat_messages where org_id = %s and buyer_id = %s order by id desc limit 20", (org, sample[org][0]))

    def by_telegram(_c, org):
        return "select * from buyers_crm where telegram_id = %s", (sample[org][1],)

    def by_telegram_explicit(_c, org):
        return "select * from buyers_crm where org_id = %s and telegram_id = %s", (org, sample[org][1])

    def reverse_match(_c, _org):
        return "select * from match_buyers_for_property(%s::uuid, -1, 50)", (prop[0],)

    ann = "select id from buyers_crm order by preference_embedding <=> %s::vector limit 50"

    def nearest(_c, _org):
        return ann, (prop[1],)

    results: list[tuple[str, tuple[float, float, int]]] = []

    def run(label: str, make_query, role: str | None, setup: tuple[str, ...] = ()) -> None:
        results.append((label, measure(url, org_ids, args.runs, make_query, role=role, setup=setup)))

    run("chat history (20 rows), owner + explicit org_id", chat_explicit, None)
    run("chat history (20 rows), app_user + RLS", chat, "app_user")
    run("buyer by telegram id, owner + explicit org_id", by_telegram_explicit, None)
    run("buyer by telegram id, app_user + RLS", by_telegram, "app_user")
    run("buyers matching a unit (50), app_user + RLS, no vector index", reverse_match, "app_user")
    run("nearest 50 buyers, app_user + RLS, exact scan", nearest, "app_user")

    with psycopg.connect(url, autocommit=True) as conn:
        t = time.perf_counter()
        conn.execute("create index bench_pref_hnsw on buyers_crm using hnsw (preference_embedding vector_cosine_ops)")
        print(f"HNSW index on {total} buyers built in {time.perf_counter() - t:.0f} s\n")
        conn.execute("analyze buyers_crm")
    run("nearest 50 buyers, app_user + RLS, HNSW, default ef_search", nearest, "app_user")
    run("nearest 50 buyers, app_user + RLS, HNSW, ef_search 1000", nearest, "app_user", ("set local hnsw.ef_search = 1000",))
    with psycopg.connect(url) as conn:
        version = conn.execute("select extversion from pg_extension where extname = 'vector'").fetchone()[0]
    if tuple(int(x) for x in version.split(".")[:2]) >= (0, 8):  # iterative index scans arrived in pgvector 0.8
        run("nearest 50 buyers, app_user + RLS, HNSW, iterative scan", nearest, "app_user",
            ("set local hnsw.iterative_scan = relaxed_order", "set local hnsw.max_scan_tuples = 20000"))
    else:
        print(f"pgvector {version} here: no iterative scan (needs 0.8), so that variant is skipped\n")

    print(f"| query ({total} buyers in {args.orgs} agencies) | p50 ms | p95 ms | rows |")
    print("|---|---:|---:|---:|")
    for label, (p50, p95, rows) in results:
        print(f"| {label} | {p50:.2f} | {p95:.2f} | {rows} |")


if __name__ == "__main__":
    main()
