"""Rehearsal of docs/MIGRATION_HOSTED.md on a scratch database that starts at migration 019 and goes through the newest one.

    python -m scripts.rehearse_hosted_migration [--buyers 300] [--msgs 5] [--audit-rows 3000] [--name proppulse_rehearsal]

Run from backend/. It uses the embedded Postgres under backend/.pgdata (the same server the tests use) and a scratch database of its
own, dropped and recreated on every run. It never reads DATABASE_URL from .env, and it refuses the test database's name, so neither the
hosted database nor `proppulse_test` can be touched.

Stages: baseline at 019 (seed + old-style data) -> Step A (020-022, old code must keep working) -> a side copy where the additive later
files (027-029, 031-033) are applied on top of Step A -> Step B (023-033, old code expected to break) -> checks on the finished database
(row counts, row-level security from the agency's role, the price views, the audit chain back-fill with `python -m scripts.verify_audit`,
tampering is caught, an agency with its own prices can be deleted). Each file is timed on its own.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, ".")
import pgserver
import psycopg

from scripts import migrate as m
from scripts.seed import main as seed
from tests.localdb import DB_NAME as TEST_DB_NAME
from tests.localdb import EMBEDDINGS

ap = argparse.ArgumentParser()
ap.add_argument("--buyers", type=int, default=300)
ap.add_argument("--msgs", type=int, default=5, help="chat messages per buyer")
ap.add_argument("--audit-rows", type=int, default=3000, help="audit entries written before migration 030 (spread over 3 agencies)")
ap.add_argument("--name", default="proppulse_rehearsal")
args = ap.parse_args()
NAME = args.name
if NAME in (TEST_DB_NAME, "postgres") or not NAME.startswith("proppulse_rehearsal"):
    sys.exit(f"refusing to use database name {NAME!r}: scratch names must start with proppulse_rehearsal")

BACKEND = Path(".").resolve()
server = pgserver.get_server(BACKEND / ".pgdata", cleanup_mode="stop")
admin = server.get_uri()
BASE = admin.rsplit("/", 1)[0]
if "supabase" in admin or "pooler" in admin:
    sys.exit("the admin URL is not the embedded server; stopping")
ORG1 = "00000000-0000-4000-8000-000000000001"
ALL = BACKEND / "migrations"
timings: dict[str, float] = {}
results: list[tuple[str, bool, str]] = []


def make_db(name: str, template: str | None = None) -> str:
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'drop database if exists "{name}" with (force)')
        c.execute(f'create database "{name}"' + (f' template "{template}"' if template else ""))
    u = f"{BASE}/{name}"
    if template is None:
        with psycopg.connect(u, autocommit=True) as c:
            c.execute("create extension if not exists vector")
    return u


def apply(url: str, lo: int, hi: int, label: str = "") -> float:
    """Run the real migrator on files lo..hi, one file at a time so each can be timed."""
    total = 0.0
    for p in sorted(ALL.glob("*.sql")):
        if not lo <= int(p.name[:3]) <= hi:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="mig_"))
        shutil.copy(p, tmp / p.name)
        m.MIGRATIONS = tmp
        t = time.perf_counter()
        m.migrate(url)
        dt = time.perf_counter() - t
        timings[p.name] = dt
        total += dt
        print(f"   {p.name:<48} {dt:7.2f} s")
        shutil.rmtree(tmp)
    print(f"   {label or f'{lo}-{hi}'} total {total:.2f} s")
    return total


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def probes(url: str, label: str) -> list[tuple[str, str]]:
    """Run each write/read the live (main) code makes; report ok or the error. Everything is rolled back."""
    out = []
    with psycopg.connect(url) as c:
        prop = c.execute("select id from properties where embedding is not null limit 1").fetchone()[0]
        n = int(time.time() * 1000) % 10**9
        tests = {
            "get_or_create_buyer (on conflict thread_id)": (
                "insert into buyers_crm (thread_id, telegram_id) values (%s, %s) on conflict (thread_id) do update set thread_id = excluded.thread_id returning *",
                (f"tg:{n}", n)),
            "claim_announcement (on conflict message_id)": (
                "insert into inbound_announcements (message_id, sender, subject, status) values (%s, 'a@b.c', 's', 'FAILED') on conflict (message_id) do nothing returning message_id",
                (f"m{n}",)),
            "unit_layouts upsert (on conflict property_id)": (
                "insert into unit_layouts (property_id, layout) values (%s, '{}'::jsonb) on conflict (property_id) do update set layout = excluded.layout returning property_id",
                (str(prop),)),
            "claim_channel_post (on conflict property_id, kind, price)": (
                "insert into channel_posts (property_id, kind, price_etb, chat_id, text) values (%s,'NEW_LISTING',%s,'c','t') on conflict (property_id, kind, price_etb) where kind <> 'MANUAL' do nothing returning *",
                (str(prop), 1000 + n % 1000)),
            "match_properties_hybrid (search)": (
                "select * from match_properties_hybrid((select embedding from properties where id = %s), null, null, null, 3, 0.0)", (str(prop),)),
            "match_buyers_for_property (reverse-RAG)": ("select * from match_buyers_for_property(%s, 0.0, 50)", (str(prop),)),
            "properties update (shared catalogue write as owner)": ("update properties set price_etb = price_etb where id = %s returning id", (str(prop),)),
        }
        for name, (sql_, params) in tests.items():
            try:
                with c.transaction():
                    c.execute(sql_, params).fetchall()
                out.append((name, "ok"))
            except Exception as exc:
                out.append((name, "FAILS: " + str(exc).splitlines()[0][:90]))
        try:
            with c.transaction():
                b = c.execute("insert into buyers_crm (thread_id, telegram_id) values (%s, %s) returning id", (f"tg:x{n}", n + 1)).fetchone()[0]
                c.execute("insert into chat_messages (buyer_id, role, content, source, telegram_file_id, attachments) values (%s,'buyer','hi','text',null,'[]'::jsonb) returning *", (b,)).fetchall()
                out.append(("insert buyer + chat message", "ok"))
                c.execute("insert into proactive_events (kind, audience, buyer_id, dedupe_key) values ('PRICE_DROP','buyer',%s,%s) on conflict (dedupe_key) do update set created_at = now() where proactive_events.status = 'pending' and proactive_events.created_at < now() - interval '10 minutes' returning *", (b, f"k{n}")).fetchall()
                out.append(("reserve_proactive (on conflict dedupe_key)", "ok"))
        except Exception as exc:
            out.append(("buyer/chat/dedupe", "FAILS: " + str(exc).splitlines()[0][:90]))
        c.rollback()
    ok = sum(1 for _, r in out if r == "ok")
    print(f"\n-- old-code probes {label}: {ok} of {len(out)} succeed")
    for name, res in out:
        print(f"   {res[:4]:<5} {name}   {res if res != 'ok' else ''}")
    return out


def counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as c:
        tabs = [r[0] for r in c.execute("select tablename from pg_tables where schemaname = 'public' and tablename not like 'checkpoint%' and tablename <> 'schema_migrations' order by 1")]
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tabs}


# ---------------------------------------------------------------------------------------------------------------------------------
print(f"== baseline: 001-019 + seed + {args.buyers} buyers x {args.msgs} messages (database {NAME})")
url = make_db(NAME)
apply(url, 1, 19)
seed(url=url, embeddings_file=str(EMBEDDINGS))
with psycopg.connect(url) as c:
    t = time.perf_counter()
    with c.cursor() as cur:
        cur.executemany("insert into buyers_crm (thread_id, telegram_id, full_name) values (%s,%s,%s)",
                        [(f"tg:old{i}", 5000 + i, f"Old {i}") for i in range(args.buyers)])
    cur2 = c.execute("select id from buyers_crm where thread_id like 'tg:old%'")
    ids = [r[0] for r in cur2]
    with c.cursor() as cur:
        cur.executemany("insert into chat_messages (buyer_id, role, content) values (%s,'buyer',%s)", [(b, f"old {k}") for b in ids for k in range(args.msgs)])
        cur.executemany("insert into model_calls (operation, lane, outcome, latency_ms, input_tokens, output_tokens, buyer_id) values ('chat','text','ok',100,500,200,%s)",
                        [(b,) for b in ids for _ in range(2)])
    c.commit()
    print(f"   data loaded in {time.perf_counter() - t:.1f} s")
before = counts(url)
print("   rows:", {k: v for k, v in before.items() if v and k in ("buyers_crm", "chat_messages", "model_calls", "properties", "projects")})
probes(url, "on 019 (control)")

# ---------------------------------------------------------------------------------------------------------------------------------
print("\n== Step A: 020-022 with the old code running")
apply(url, 20, 22, "Step A")
with psycopg.connect(url) as c:
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    print("   organizations:", q("select count(*) from organizations"), "| plan of agency #1:", q(f"select plan from organizations where id = '{ORG1}'"),
          "| rolcreaterole:", q("select rolcreaterole from pg_roles where rolname = current_user"), "| superuser:", q("select rolsuper from pg_roles where rolname = current_user"))
    check("A: one organization (proppulse)", q("select count(*) from organizations") == 1)
    check("A: no row without org_id", q("select count(*) from buyers_crm where org_id is null") == 0)
    check("A: all chat messages in agency #1", q(f"select count(*) from chat_messages where org_id = '{ORG1}'") == before["chat_messages"])
    check("A: RLS on buyers_crm", q("select relrowsecurity from pg_class where relname = 'buyers_crm'") is True)
    check("A: table owner not forced through RLS", q("select relforcerowsecurity from pg_class where relname = 'buyers_crm'") is False)
    check("A: current user is a member of app_user", q("select pg_has_role(current_user, 'app_user', 'member')") is True)
afterA = counts(url)
check("A: row counts of the 019 tables unchanged", all(afterA[t] == v for t, v in before.items()), "")
pa = probes(url, "after 020-022 (old code)")
check("A: every old-code probe still succeeds", all(r == "ok" for _, r in pa))

# ---------------------------------------------------------------------------------------------------------------------------------
SIDE = NAME + "_sidecopy"
print("\n== Side experiment: are the later additive files (027-029, 031-033) also safe on top of Step A with the old code?")
with psycopg.connect(admin, autocommit=True) as c:
    c.execute('select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()', (NAME,))
side = make_db(SIDE, template=NAME)
# 030 needs audit_log (026), which belongs to the switch; the rest do not need 023-026.
for lo in (27, 28, 29, 31, 32, 33):
    apply(side, lo, lo, f"{lo}")
ps = probes(side, "after 020-022 + 027-029,031-033 (old code)")
check("Side: old code still works with 027-029, 031-033 applied early", all(r == "ok" for _, r in ps))
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(f'drop database "{SIDE}" with (force)')

# ---------------------------------------------------------------------------------------------------------------------------------
print("\n== Step B: 023-029 (switch)")
apply(url, 23, 26, "023-026")
pb = probes(url, "after 023-026 (old code, expected to break)")
check("B: old code breaks at 023 as the plan says (>= 4 probes fail)", sum(1 for _, r in pb if r != "ok") >= 4)
afterB = counts(url)
check("B: row counts unchanged by 023-026", all(afterB[t] == v for t, v in before.items()))

# audit entries written by the new code before 030 exists (the back-fill chains them)
print(f"\n   writing {args.audit_rows} audit entries over 3 agencies before 030")
orgs = [ORG1, str(uuid.uuid4()), str(uuid.uuid4())]
with psycopg.connect(url) as c:
    for i, o in enumerate(orgs[1:], 2):
        c.execute("insert into organizations (id, slug, name, plan) values (%s, %s, %s, 'free')", (o, f"rehearsal-{i}", f"Rehearsal {i}"))
        c.execute("insert into org_settings (org_id, display_name) values (%s, %s) on conflict do nothing", (o, f"Rehearsal {i}"))
    with c.cursor() as cur:
        cur.executemany("insert into audit_log (org_id, actor_kind, action, target, detail) values (%s,'system',%s,%s,'{\"n\": 1}'::jsonb)",
                        [(orgs[i % 3], "rehearsal.event", f"t{i}") for i in range(args.audit_rows)])
    c.commit()

apply(url, 27, 99, "027-newest")

# ---------------------------------------------------------------------------------------------------------------------------------
print("\n== Checks on the finished database")
afterAll = counts(url)
check("row counts of every 019 table unchanged after 033", all(afterAll[t] == v for t, v in before.items()))
with psycopg.connect(url) as c:
    q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731
    newest = len(list(ALL.glob("*.sql")))
    check(f"schema_migrations has all {newest} files", q("select count(*) from schema_migrations") == newest, str(q("select max(name) from schema_migrations")))
    check("audit back-fill: every entry has seq/hash", q("select count(*) from audit_log where hash is null or seq is null") == 0, f"{q('select count(*) from audit_log')} entries")
    check("audit chain: seq runs 1..n per agency", q("select count(*) from (select org_id, count(*) n, max(seq) m from audit_log group by org_id) s where n <> m") == 0)

    # RLS from the agency's role: agency #1 sees its buyers, a new agency sees none, and neither sees the jobs/audit-state tables.
    def as_org(org: str, sql_: str):
        with c.transaction():
            c.execute("select set_config('app.org_id', %s, true)", (org,))
            c.execute("set local role app_user")
            return c.execute(sql_).fetchone()[0]
    n1 = as_org(ORG1, "select count(*) from buyers_crm")
    n2 = as_org(orgs[1], "select count(*) from buyers_crm")
    check("RLS: agency #1 sees all its buyers", n1 == before["buyers_crm"], str(n1))
    check("RLS: a second agency sees none", n2 == 0, str(n2))
    check("RLS: second agency sees no chat messages", as_org(orgs[1], "select count(*) from chat_messages") == 0)
    check("RLS: second agency cannot see agency #1's audit entries", as_org(orgs[1], "select count(*) from audit_log where org_id <> current_org()") == 0)
    try:
        as_org(orgs[1], "select count(*) from jobs")
        check("RLS: app_user has no access to jobs", False, "select succeeded")
    except psycopg.errors.InsufficientPrivilege:
        c.rollback()
        check("RLS: app_user has no access to jobs", True)
    try:
        as_org(ORG1, "update properties set price_etb = price_etb + 1")
        check("025: app_user cannot write the shared catalogue", False, "update succeeded")
    except psycopg.errors.InsufficientPrivilege:
        c.rollback()
        check("025: app_user cannot write the shared catalogue", True)
    # a write that names another agency is refused by the policy
    try:
        with c.transaction():
            c.execute("select set_config('app.org_id', %s, true)", (orgs[1],))
            c.execute("set local role app_user")
            c.execute("insert into buyers_crm (org_id, thread_id, telegram_id) values (%s, 'tg:evil', 999999)", (ORG1,))
        check("RLS: insert into another agency refused", False)
    except psycopg.errors.InsufficientPrivilege:
        c.rollback()
        check("RLS: insert into another agency refused", True)

    # price views
    prop, shared = c.execute("select id, price_etb from properties where embedding is not null and status = 'AVAILABLE' limit 1").fetchone()
    own = float(shared) * 0.9
    c.execute("insert into agency_prices (org_id, target_kind, target_id, price_etb) values (%s,'property',%s,%s)", (ORG1, prop, own))
    c.commit()
    p1 = as_org(ORG1, f"select price_etb from agency_properties where id = '{prop}'")
    p2 = as_org(orgs[1], f"select price_etb from agency_properties where id = '{prop}'")
    check("prices: agency #1 sees its own price in the view", float(p1) == round(own, 2), f"{p1} vs shared {shared}")
    check("prices: another agency sees the shared price", p2 == shared, str(p2))
    check("prices: effective_price agrees", float(as_org(ORG1, f"select effective_price('property', '{prop}')")) == round(own, 2))
    check("prices: match_properties_hybrid returns the agency price", float(as_org(ORG1, f"select price_etb from match_properties_hybrid((select embedding from properties where id='{prop}'), null, null, null, 1, 0.0) limit 1")) == round(own, 2))
    check("029: the price change wrote history for agency #1 only", q("select count(*) from agency_price_history where org_id = %s", ORG1) == 1 and q("select count(*) from agency_price_history where org_id <> %s", ORG1) == 0)
    # 033: deleting an agency that has its own prices must work
    c.execute("insert into agency_prices (org_id, target_kind, target_id, price_etb) values (%s,'property',%s,%s)", (orgs[2], prop, own))
    c.commit()
    try:
        c.execute("delete from organizations where id = %s", (orgs[2],))
        c.commit()
        check("033: deleting an agency that has its own prices works", True)
    except Exception as exc:
        c.rollback()
        check("033: deleting an agency that has its own prices works", False, str(exc).splitlines()[0])
    # one timed insert after the back-fill: the chain trigger takes the next seq
    c.execute("insert into audit_log (org_id, actor_kind, action) values (%s,'system','rehearsal.after')", (ORG1,))
    c.commit()
    check("030: a new entry continues the chain", q("select max(seq) = count(*) from audit_log where org_id = %s", ORG1) is True)

# verify_audit as an operator would run it (own process, DATABASE_URL pointed at the scratch database only)
env = {**os.environ, "DATABASE_URL": url, "AUDIT_EXPORT_DIR": "", "AUDIT_EXPORT_BUCKET": ""}
t = time.perf_counter()
r = subprocess.run([sys.executable, "-m", "scripts.verify_audit"], env=env, capture_output=True, text=True)
timings["verify_audit"] = time.perf_counter() - t
print("   verify_audit:", (r.stdout + r.stderr).strip().splitlines()[-1:] or "", f"({timings['verify_audit']:.2f} s)")
check("verify_audit passes on the back-filled chain", r.returncode == 0, f"exit {r.returncode}")
with psycopg.connect(url, autocommit=True) as c:
    c.execute("alter table audit_log disable trigger audit_log_immutable")
    c.execute("update audit_log set action = 'tampered.x' where org_id = %s and seq = 5", (ORG1,))
    c.execute("alter table audit_log enable trigger audit_log_immutable")
r = subprocess.run([sys.executable, "-m", "scripts.verify_audit"], env=env, capture_output=True, text=True)
check("verify_audit catches an edited entry (exit 1)", r.returncode == 1, f"exit {r.returncode}")

# ---------------------------------------------------------------------------------------------------------------------------------
print("\n== Summary")
print("   per-file timings (s):", {k: round(v, 2) for k, v in timings.items()})
bad = [n for n, ok, _ in results if not ok]
print(f"   checks: {len(results) - len(bad)} passed, {len(bad)} failed")
for n in bad:
    print("   FAILED:", n)
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(f'drop database if exists "{SIDE}" with (force)')
sys.exit(1 if bad else 0)
