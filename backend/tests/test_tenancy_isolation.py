"""Row-level security keeps organizations apart, whatever the application code does."""
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest

from app.repository import Repository
from app.tenancy import use_tenant
from tests.conftest import SingleConnectionPool

PRIVATE_TABLES = [
    "buyers_crm", "chat_messages", "tours_and_appointments", "tour_properties", "commission_ledger",
    "lead_score_history", "saved_homes", "proactive_events", "price_requests", "channel_posts",
    "project_posts", "inbound_announcements", "unit_layouts", "model_calls", "event_log",
]
SHARED_TABLES = ["developers", "projects", "project_media", "price_history", "crawl_runs", "ingest_runs"]


async def make_org(conn, label: str):
    cur = await conn.execute(
        "insert into organizations (slug, name) values (%s, %s) returning id", (f"iso-{label}-{uuid4().hex[:8]}", label)
    )
    return (await cur.fetchone())["id"]


async def make_buyer(conn, org, thread: str):
    """Created as the table owner, which is how a migration or an operator would."""
    cur = await conn.execute(
        "insert into buyers_crm (org_id, thread_id) values (%s, %s) returning id", (org, f"{thread}-{uuid4().hex[:6]}")
    )
    return (await cur.fetchone())["id"]


@asynccontextmanager
async def as_org(conn, org):
    """Run statements the way the application does: labelled with the organization, as the restricted role."""
    await conn.execute("select set_config('app.org_id', %s, true)", (str(org) if org else "",))
    await conn.execute("set local role app_user")
    try:
        yield conn
    finally:
        await conn.execute("reset role")


async def scalar(conn, query, params=()):
    cur = await conn.execute(query, params)
    return (await cur.fetchone())["n"]


@pytest.fixture
async def two_orgs(db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    buyer_a, buyer_b = await make_buyer(db_conn, a, "a"), await make_buyer(db_conn, b, "b")
    return a, b, buyer_a, buyer_b


async def test_each_organization_sees_only_its_own_rows(db_conn, two_orgs):
    a, b, buyer_a, buyer_b = two_orgs
    async with as_org(db_conn, a):
        cur = await db_conn.execute("select id from buyers_crm")
        assert {r["id"] for r in await cur.fetchall()} == {buyer_a}
    async with as_org(db_conn, b):
        cur = await db_conn.execute("select id from buyers_crm")
        assert {r["id"] for r in await cur.fetchall()} == {buyer_b}


@pytest.mark.parametrize("table", PRIVATE_TABLES)
async def test_no_label_means_no_rows_on_any_private_table(db_conn, two_orgs, table):
    async with as_org(db_conn, None):
        assert await scalar(db_conn, f"select count(*) as n from {table}") == 0


async def test_a_tenant_cannot_write_into_another_organization(db_conn, two_orgs):
    a, b, *_ = two_orgs
    async with as_org(db_conn, a):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with db_conn.transaction():
                await db_conn.execute("insert into buyers_crm (org_id, thread_id) values (%s, 'sneaky')", (b,))


async def test_a_write_without_an_organization_takes_the_callers(db_conn, two_orgs):
    a, *_ = two_orgs
    async with as_org(db_conn, a):
        cur = await db_conn.execute("insert into buyers_crm (thread_id) values ('mine') returning org_id")
        assert (await cur.fetchone())["org_id"] == a


async def test_an_unlabelled_write_is_refused_for_the_app_role(db_conn, two_orgs):
    async with as_org(db_conn, None):
        with pytest.raises(psycopg.errors.Error):  # the default falls back to tenant one, which fails the policy for null
            async with db_conn.transaction():
                await db_conn.execute("insert into buyers_crm (thread_id) values ('orphan')")


async def test_updates_and_deletes_cannot_reach_another_organization(db_conn, two_orgs):
    a, b, buyer_a, buyer_b = two_orgs
    async with as_org(db_conn, a):
        cur = await db_conn.execute("update buyers_crm set full_name = 'hijacked' where id = %s", (buyer_b,))
        assert cur.rowcount == 0
        cur = await db_conn.execute("delete from buyers_crm where id = %s", (buyer_b,))
        assert cur.rowcount == 0
    assert await scalar(db_conn, "select count(*) as n from buyers_crm where id = %s and full_name is null", (buyer_b,)) == 1


async def test_a_row_cannot_be_moved_to_another_organization(db_conn, two_orgs):
    a, b, buyer_a, _ = two_orgs
    async with as_org(db_conn, a):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with db_conn.transaction():
                await db_conn.execute("update buyers_crm set org_id = %s where id = %s", (b, buyer_a))
    assert await scalar(db_conn, "select count(*) as n from buyers_crm where id = %s and org_id = %s", (buyer_a, a)) == 1


async def test_market_listings_are_shared_and_exclusive_ones_are_not(db_conn, two_orgs):
    a, b, *_ = two_orgs
    cur = await db_conn.execute(
        "update properties set org_id = %s where id = (select id from properties where org_id is null limit 1) returning id", (a,)
    )
    exclusive = (await cur.fetchone())["id"]
    async with as_org(db_conn, a):
        assert await scalar(db_conn, "select count(*) as n from properties where id = %s", (exclusive,)) == 1
        market = await scalar(db_conn, "select count(*) as n from properties where org_id is null")
    async with as_org(db_conn, b):
        assert await scalar(db_conn, "select count(*) as n from properties where id = %s", (exclusive,)) == 0
        assert await scalar(db_conn, "select count(*) as n from properties where org_id is null") == market > 0


@pytest.mark.parametrize("table", SHARED_TABLES)
async def test_shared_market_tables_are_readable_by_every_organization(db_conn, two_orgs, table):
    a, b, *_ = two_orgs
    owner_count = await scalar(db_conn, f"select count(*) as n from {table}")
    for org in (a, b):
        async with as_org(db_conn, org):
            assert await scalar(db_conn, f"select count(*) as n from {table}") == owner_count


async def test_tenancy_tables_expose_only_the_callers_own_organization(db_conn, two_orgs):
    a, b, *_ = two_orgs
    user = uuid4()
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner'), (%s, %s, 'owner')", (a, user, b, uuid4()))
    async with as_org(db_conn, a):
        cur = await db_conn.execute("select id from organizations")
        assert [r["id"] for r in await cur.fetchall()] == [a]
        cur = await db_conn.execute("select org_id from memberships")
        assert {r["org_id"] for r in await cur.fetchall()} == {a}
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with db_conn.transaction():
                await db_conn.execute("insert into organizations (slug, name) values ('grab', 'grab')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with db_conn.transaction():
                await db_conn.execute("update memberships set role = 'owner'")


# ---- the repository, end to end ----

async def test_repository_calls_only_see_the_organization_in_scope(db_conn, two_orgs):
    a, b, buyer_a, buyer_b = two_orgs
    repo = Repository(SingleConnectionPool(db_conn))
    with use_tenant(a):
        assert await repo.get_buyer(buyer_a) is not None
        assert await repo.get_buyer(buyer_b) is None, "another organization's buyer does not exist from here"
        assert {x["id"] for x in await repo.list_buyers(500)} >= {buyer_a}
        assert buyer_b not in {x["id"] for x in await repo.list_buyers(500)}
    with use_tenant(b):
        assert await repo.get_buyer(buyer_a) is None
        with pytest.raises(LookupError):
            await repo.update_buyer(buyer_a, full_name="hijacked")  # an update of a row it cannot see finds nothing
    assert await scalar(db_conn, "select count(*) as n from buyers_crm where id = %s and full_name is null", (buyer_a,)) == 1


async def test_repository_writes_land_in_the_organization_in_scope(db_conn, two_orgs):
    a, b, *_ = two_orgs
    repo = Repository(SingleConnectionPool(db_conn))
    with use_tenant(b):
        buyer = await repo.get_or_create_buyer(f"tg:{uuid4().hex[:8]}")
    assert buyer["org_id"] == b != a


# ---- the guard rails around the policies ----

async def test_app_role_cannot_bypass_row_level_security(db_conn):
    cur = await db_conn.execute("select rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname = 'app_user'")
    assert dict(await cur.fetchone()) == {"rolsuper": False, "rolbypassrls": False, "rolcanlogin": False}
    cur = await db_conn.execute(
        "select count(*) as n from pg_class where relnamespace = 'public'::regnamespace and pg_get_userbyid(relowner) = 'app_user'"
    )
    assert (await cur.fetchone())["n"] == 0, "a table owner is not subject to row-level security"


async def test_every_table_the_app_role_can_touch_has_row_level_security_on(db_conn):
    cur = await db_conn.execute(
        "select c.relname, c.relrowsecurity from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' "
        "and has_table_privilege('app_user', c.oid, 'select')"
    )
    unprotected = [r["relname"] for r in await cur.fetchall() if not r["relrowsecurity"]]
    assert not unprotected, f"readable by app_user but without row-level security: {unprotected}"


async def test_every_private_table_has_a_policy_that_checks_the_organization(db_conn):
    cur = await db_conn.execute(
        "select tablename, qual, with_check from pg_policies where schemaname = 'public' and 'app_user' = any(roles)"
    )
    policies = {}
    for r in await cur.fetchall():
        policies.setdefault(r["tablename"], []).append(r)
    for table in PRIVATE_TABLES:
        assert any("current_org()" in (p["qual"] or "") and "current_org()" in (p["with_check"] or "") for p in policies[table]), table


async def test_no_function_runs_with_its_owners_rights(db_conn):
    """A SECURITY DEFINER function would read around the policies; vector search must run as the caller."""
    cur = await db_conn.execute(
        "select proname from pg_proc where pronamespace = 'public'::regnamespace and prosecdef"
    )
    assert [r["proname"] for r in await cur.fetchall()] == []
