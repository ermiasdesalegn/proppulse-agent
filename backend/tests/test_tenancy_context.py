"""The organization label that every repository call puts on its transaction."""
import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.repository import Repository
from app.tenancy import (
    DEFAULT_ORG_ID,
    MissingTenant,
    TenantContext,
    current_tenant,
    resolve_org_id,
    tenant_connection,
    use_tenant,
)
from tests.conftest import SingleConnectionPool

ORG_A, ORG_B = uuid4(), uuid4()


async def _label(conn) -> str | None:
    cur = await conn.execute("select current_setting('app.org_id', true) as v")
    return (await cur.fetchone())["v"]


def test_context_is_scoped_and_restored():
    assert current_tenant() is None
    with use_tenant(ORG_A) as outer:
        assert current_tenant() == outer and outer.org_id == ORG_A
        with use_tenant(ORG_B):
            assert resolve_org_id() == ORG_B
        assert resolve_org_id() == ORG_A
    assert current_tenant() is None


def test_resolution_order_and_strict_mode():
    assert resolve_org_id() == DEFAULT_ORG_ID, "legacy fallback while strict mode is off"
    with pytest.raises(MissingTenant):
        resolve_org_id(strict=True)
    with use_tenant(ORG_A):
        assert resolve_org_id(ORG_B) == ORG_B, "a pinned organization wins over the one in scope"
        assert resolve_org_id(strict=True) == ORG_A


def test_roles_are_ordered_and_checked():
    admin = TenantContext(ORG_A, role="admin")
    assert admin.at_least("agent") and admin.at_least("admin") and not admin.at_least("owner")
    assert not TenantContext(ORG_A).at_least("viewer"), "no role (a webhook, a job) never passes"
    with pytest.raises(ValueError):
        TenantContext(ORG_A, role="god")


@pytest_asyncio.fixture
async def real_pool():
    """A real pool with a single physical connection, so every caller shares it in turn."""
    pool = AsyncConnectionPool(
        get_settings().database_url, min_size=1, max_size=1, open=False,
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
    )
    await pool.open()
    yield pool
    await pool.close()


async def test_label_is_visible_inside_and_gone_after(real_pool):
    async with tenant_connection(real_pool, ORG_A) as conn:
        assert await _label(conn) == str(ORG_A)
    async with real_pool.connection() as conn:  # same physical connection, next caller
        assert await _label(conn) in (None, "")


async def test_rollback_on_error_undoes_the_work(real_pool):
    with pytest.raises(RuntimeError):
        async with tenant_connection(real_pool, ORG_A, role=None) as conn:
            await conn.execute("insert into organizations (slug, name) values ('rolled-back', 'x')")
            raise RuntimeError("boom")
    async with real_pool.connection() as conn:
        cur = await conn.execute("select 1 from organizations where slug = 'rolled-back'")
        assert await cur.fetchone() is None


async def test_a_caller_that_owns_its_transaction_stays_in_control(db_conn):
    """The tenant connection adds no commit of its own: this is what lets the other tests roll their data back."""
    async with tenant_connection(SingleConnectionPool(db_conn), ORG_A, role=None) as conn:
        await conn.execute("insert into organizations (slug, name) values ('kept-until-rollback', 'x')")
    await db_conn.rollback()
    cur = await db_conn.execute("select 1 from organizations where slug = 'kept-until-rollback'")
    assert await cur.fetchone() is None


async def test_repository_labels_every_call_with_the_pinned_or_scoped_org(db_conn):
    seen: list[str | None] = []

    class Spy(Repository):
        async def probe(self) -> None:
            async with self._conn() as conn:
                seen.append(await _label(conn))

    pool = SingleConnectionPool(db_conn)
    await Spy(pool).probe()                       # nothing in scope: the legacy organization
    with use_tenant(ORG_A):
        await Spy(pool).probe()                   # the one in scope
        await Spy(pool, ORG_B).probe()            # a pinned one wins
    assert seen == [str(DEFAULT_ORG_ID), str(ORG_A), str(ORG_B)]
    with pytest.raises(MissingTenant):
        await Spy(pool, strict=True).probe()


async def test_a_real_pool_never_leaks_one_org_into_the_next_caller(real_pool):
    """One physical connection shared by many interleaved callers: each sees only its own label, and none sees a stale one."""
    orgs = [uuid4() for _ in range(12)]

    async def caller(org: UUID) -> tuple[UUID, str | None, str | None]:
        await asyncio.sleep(0)
        async with tenant_connection(real_pool, org) as conn:
            before = await _label(conn)
            await asyncio.sleep(0.01)  # the others queue for the single connection meanwhile
            after = await _label(conn)
        return org, before, after

    results = await asyncio.gather(*(caller(o) for o in orgs))
    assert all(b == a == str(o) for o, b, a in results)
    async with real_pool.connection() as conn:  # an unlabelled caller afterwards sees nothing
        assert await _label(conn) in (None, "")


async def test_work_started_inside_a_scope_inherits_it():
    async def child() -> UUID:
        return resolve_org_id()

    with use_tenant(ORG_A):
        assert await asyncio.create_task(child()) == ORG_A
    assert await asyncio.create_task(child()) == DEFAULT_ORG_ID
