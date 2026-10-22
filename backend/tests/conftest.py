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
