from contextlib import asynccontextmanager

import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import get_settings
from app.repository import Repository


class SingleConnectionPool:
    """Stands in for the pool so a test can run every repository call on one connection and roll it all back."""

    def __init__(self, conn: AsyncConnection):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


@pytest_asyncio.fixture
async def db_conn():
    conn = await AsyncConnection.connect(
        get_settings().database_url, row_factory=dict_row, autocommit=False, prepare_threshold=None
    )
    yield conn
    await conn.rollback()
    await conn.close()


@pytest_asyncio.fixture
async def repo(db_conn):
    return Repository(SingleConnectionPool(db_conn))


@pytest_asyncio.fixture
async def quiet_world(db_conn):
    """Hide real activity from a test. The real Telegram buyer and the real alert history live in the same
    database as the test data; inside the test's own transaction (always rolled back) they are set aside, so
    expectations like 'only this buyer is alerted' stay true however much real use the database has seen."""
    await db_conn.execute("update buyers_crm set deal_stage = 'LOST' where thread_id not like 'test:%'")
    await db_conn.execute("delete from proactive_events")


@pytest_asyncio.fixture
async def spy_repo(db_conn):
    from tests.agent_helpers import SpyRepo

    return SpyRepo(SingleConnectionPool(db_conn))


@pytest_asyncio.fixture
async def vector_of(db_conn):
    async def get(title_like: str) -> list[float]:
        cur = await db_conn.execute("select embedding::text as e from properties where title like %s", (title_like,))
        return [float(x) for x in (await cur.fetchone())["e"].strip("[]").split(",")]

    return get
