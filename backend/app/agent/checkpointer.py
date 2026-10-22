from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

log = logging.getLogger(__name__)

# Created by AsyncPostgresSaver.setup(); they must not be readable through Supabase's public API.
CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


@dataclass
class Checkpointer:
    saver: Any
    kind: str  # "postgres" or "memory"
    _pool: AsyncConnectionPool | None = None

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()


async def open_checkpointer(settings: Settings, *, allow_fallback: bool = True) -> Checkpointer:
    """Postgres-backed checkpoints so conversations survive restarts; MemorySaver if Postgres is unusable."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=3,
        open=False,
        # The saver requires autocommit connections that return dict rows.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    )
    try:
        await pool.open(wait=True, timeout=20)
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        async with pool.connection() as conn:
            for table in CHECKPOINT_TABLES:
                await conn.execute(f"alter table {table} enable row level security")
        return Checkpointer(saver=saver, kind="postgres", _pool=pool)
    except Exception:
        await pool.close()
        if not allow_fallback:
            raise
        log.warning("PostgresSaver unavailable, falling back to MemorySaver (state is lost on restart)", exc_info=True)
        return Checkpointer(saver=MemorySaver(), kind="memory")
