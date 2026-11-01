"""The tenancy data model: every table is either shared market data or carries a NOT NULL org_id."""
import psycopg
import pytest

TENANT_ONE = "00000000-0000-4000-8000-000000000001"

# Shared market data: the crawl runs once and every organization reads it. A new table must be placed in one of the two groups.
SHARED_TABLES = {
    "developers", "projects", "project_media", "price_history", "crawl_runs", "ingest_runs", "properties",
    "schema_migrations",
}
TENANCY_TABLES = {"organizations", "memberships", "invitations", "org_settings"}
LIBRARY_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}  # LangGraph's own


async def _tables(conn):
    cur = await conn.execute("select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE'")
    return {r["table_name"] for r in await cur.fetchall()}


async def test_every_table_is_shared_or_tenant_scoped(db_conn):
    cur = await db_conn.execute(
        "select table_name from information_schema.columns "
        "where table_schema = 'public' and column_name = 'org_id' and is_nullable = 'NO'"
    )
    scoped = {r["table_name"] for r in await cur.fetchall()}
    unplaced = await _tables(db_conn) - scoped - SHARED_TABLES - TENANCY_TABLES - LIBRARY_TABLES
    assert not unplaced, f"decide whether these are shared or private (add org_id NOT NULL): {sorted(unplaced)}"


async def test_properties_org_id_is_nullable_for_market_listings(db_conn):
    cur = await db_conn.execute(
        "select is_nullable from information_schema.columns where table_name = 'properties' and column_name = 'org_id'"
    )
    assert (await cur.fetchone())["is_nullable"] == "YES"


async def test_tenant_one_exists_with_settings(db_conn):
    cur = await db_conn.execute("select slug, plan from organizations where id = %s", (TENANT_ONE,))
    assert dict(await cur.fetchone()) == {"slug": "proppulse", "plan": "business"}
    cur = await db_conn.execute("select 1 as ok from org_settings where org_id = %s", (TENANT_ONE,))
    assert await cur.fetchone()


async def test_existing_rows_belong_to_tenant_one(db_conn):
    cur = await db_conn.execute("select count(*) as n from buyers_crm where org_id <> %s", (TENANT_ONE,))
    assert (await cur.fetchone())["n"] == 0


async def test_slug_must_be_a_safe_subdomain(db_conn):
    for bad in ("A", "-x-", "has space", "UPPER", "a" * 41):
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db_conn.transaction():
                await db_conn.execute("insert into organizations (slug, name) values (%s, 'x')", (bad,))


async def test_last_owner_cannot_be_removed_or_demoted(db_conn):
    cur = await db_conn.execute("insert into organizations (slug, name) values ('acme', 'Acme') returning id")
    org = (await cur.fetchone())["id"]
    uid = "11111111-1111-4111-8111-111111111111"
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (org, uid))
    with pytest.raises(psycopg.errors.RaiseException):
        async with db_conn.transaction():
            await db_conn.execute("update memberships set role = 'admin' where org_id = %s", (org,))
    with pytest.raises(psycopg.errors.RaiseException):
        async with db_conn.transaction():
            await db_conn.execute("delete from memberships where org_id = %s", (org,))
    # a second owner makes the first one removable
    await db_conn.execute(
        "insert into memberships (org_id, user_id, role) values (%s, '22222222-2222-4222-8222-222222222222', 'owner')", (org,)
    )
    await db_conn.execute("delete from memberships where org_id = %s and user_id = %s", (org, uid))
    # and deleting the whole organization is never blocked by the owner rule
    await db_conn.execute("delete from organizations where id = %s", (org,))


async def test_composite_key_targets_exist(db_conn):
    cur = await db_conn.execute(
        "select count(*) as n from pg_constraint where conname in ('buyers_crm_org_id_key', 'tours_and_appointments_org_id_key')"
    )
    assert (await cur.fetchone())["n"] == 2
