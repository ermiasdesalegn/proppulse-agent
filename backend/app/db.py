from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings


def make_pool(settings: Settings | None = None) -> AsyncConnectionPool:
    """Pool is created closed; call `await pool.open()` at app start and `await pool.close()` at shutdown."""
    s = settings or get_settings()
    return AsyncConnectionPool(
        s.database_url,
        min_size=1,
        max_size=5,
        open=False,
        # prepare_threshold=None keeps this working behind Supabase's transaction pooler as well.
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
    )
