"""Keeps a row for every Gemini request attempt, and tells the dashboard about it.

`record` is called from the gateway on every attempt and must never block or raise, so rows go through a bounded queue and a
background writer inserts them in small batches. If the database is slow the oldest rows are dropped, never a buyer's reply.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from app.tenancy import resolve_org_id

log = logging.getLogger(__name__)

COLUMNS = ("operation", "schema_name", "lane", "model", "outcome", "latency_ms", "attempt", "fallbacks",
           "input_tokens", "output_tokens", "node", "buyer_id", "thread_id", "org_id")


def to_row(record: dict) -> dict:
    row = {c: record.get(c) for c in COLUMNS}
    row["org_id"] = str(resolve_org_id())  # recorded in the caller's task, so it is the organization the model call was made for
    row["schema_name"] = record.get("schema")
    row["fallbacks"] = list(record.get("fallbacks") or [])
    return row


class ModelCallLog:
    def __init__(self, pool: Any, events: Any = None, *, capacity: int = 1000, batch: int = 50):
        self._pool, self._events, self._batch = pool, events, batch
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=capacity)
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def record(self, record: dict) -> None:
        row = to_row(record)
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped += 1
            return
        if self._events is not None:
            self._events.publish("model_call", **{k: v for k, v in row.items() if k != "org_id" and (k not in ("buyer_id", "thread_id") or v)})

    async def start(self) -> None:
        self._task = asyncio.create_task(self._writer(), name="model-call-log")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.flush()

    async def flush(self) -> None:
        rows = []
        while not self._queue.empty():
            rows.append(self._queue.get_nowait())
        if rows:
            await self._insert(rows)

    async def _insert(self, rows: list[dict]) -> None:
        cols = ", ".join(COLUMNS)
        marks = ", ".join(f"%({c})s" for c in COLUMNS)
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(f"insert into model_calls ({cols}) values ({marks})", rows)
        except Exception as exc:
            self.dropped += len(rows)
            log.warning("model calls could not be stored (%s)", type(exc).__name__)

    async def _writer(self) -> None:
        while True:
            rows = [await self._queue.get()]
            await asyncio.sleep(0.2)  # let a burst (one turn makes several calls) arrive together
            while len(rows) < self._batch and not self._queue.empty():
                rows.append(self._queue.get_nowait())
            await self._insert(rows)
