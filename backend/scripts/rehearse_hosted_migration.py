"""Rehearsal of docs/MIGRATION_HOSTED.md on a scratch database that starts at migration 019.

Old-code probes are the exact SQL the live (main) repository runs. Run from backend/:  python -m scripts.rehearse_hosted_migration  (drops and recreates the scratch database `proppulse_rehearsal`).
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
import pgserver
import psycopg

from scripts import migrate as m
from scripts.seed import main as seed
from tests.localdb import EMBEDDINGS

BACKEND = Path(".").resolve()
NAME = "proppulse_rehearsal"
server = pgserver.get_server(BACKEND / ".pgdata", cleanup_mode="stop")
admin = server.get_uri()
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(f'drop database if exists "{NAME}" with (force)')
    c.execute(f'create database "{NAME}"')
url = admin.rsplit("/", 1)[0] + f"/{NAME}"
with psycopg.connect(url, autocommit=True) as c:
    c.execute("create extension if not exists vector")

ALL = BACKEND / "migrations"


def apply(lo: int, hi: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mig_"))
    for p in ALL.glob("*.sql"):
        if lo <= int(p.name[:3]) <= hi:
            shutil.copy(p, tmp / p.name)
    m.MIGRATIONS = tmp
    t = time.perf_counter()
    m.migrate(url)
    print(f"   ({time.perf_counter() - t:.2f} s)")
    shutil.rmtree(tmp)


def probes(label: str) -> list[tuple[str, str]]:
    """Run each of the live code's write statements; report ok or the error."""
    out = []
    with psycopg.connect(url) as c:
        prop = c.execute("select id from properties limit 1").fetchone()[0]
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
        }
        for name, (sql, params) in tests.items():
            try:
                with c.transaction():
                    c.execute(sql, params).fetchall()
                out.append((name, "ok"))
            except Exception as exc:
                out.append((name, "FAILS: " + str(exc).splitlines()[0][:90]))
        # chat message + dedupe need a buyer
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
    print(f"\n-- old-code probes {label}")
    for name, res in out:
        print(f"   {res[:4]:<5} {name}   {res if res != 'ok' else ''}")
    return out


print("== baseline: 001-019 + seed + some old-style data")
apply(1, 19)
seed(url=url, embeddings_file=str(EMBEDDINGS))
with psycopg.connect(url) as c:
    for i in range(300):
        b = c.execute("insert into buyers_crm (thread_id, telegram_id, full_name) values (%s,%s,%s) returning id", (f"tg:old{i}", 5000 + i, f"Old {i}")).fetchone()[0]
        for k in range(5):
            c.execute("insert into chat_messages (buyer_id, role, content) values (%s,'buyer',%s)", (b, f"old {k}"))
    c.commit()
probes("on 019 (control)")

print("\n== Step A: 020-022 with old code")
apply(20, 22)
with psycopg.connect(url) as c:

    def q(sql):
        return c.execute(sql).fetchone()[0]

    print("   organizations:", q("select count(*) from organizations"), "| buyers without org:", q("select count(*) from buyers_crm where org_id is null"),
          "| chat rows in org 1:", q("select count(*) from chat_messages where org_id = '00000000-0000-4000-8000-000000000001'"),
          "| rls on buyers_crm:", q("select relrowsecurity from pg_class where relname = 'buyers_crm'"),
          "| member of app_user:", q("select pg_has_role(current_user, 'app_user', 'member')"),
          "| rolcreaterole:", q("select rolcreaterole from pg_roles where rolname = current_user"),
          "| superuser:", q("select rolsuper from pg_roles where rolname = current_user"))
probes("after 020-022 (old code)")

print("\n== Step B: 023-024")
apply(23, 24)
probes("after 023-024 (old code, expected to break)")

with psycopg.connect(url) as c:
    print("\n-- data check: buyers", c.execute("select count(*) from buyers_crm").fetchone()[0],
          "messages", c.execute("select count(*) from chat_messages").fetchone()[0])
