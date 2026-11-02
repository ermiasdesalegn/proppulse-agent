"""Running queued jobs inside a test: the app a test builds has no background worker, so the test runs the queue itself."""
from __future__ import annotations

from typing import Any

from app.jobs import JobWorker, queue_of


def app_of(client: Any) -> Any:
    return client._transport.app


async def run_queued(client: Any) -> list[dict]:
    """Run every due job of the test's app, one at a time, and return the finished rows (with each job's result)."""
    state = app_of(client).state
    queue = queue_of(state)
    worker = JobWorker(queue, state)
    ran: list[int] = []
    while (claimed := await worker.run_one()) is not None:
        ran.append(int(claimed["id"]))
    return [await queue.get(i) for i in ran]  # type: ignore[misc]


def total(rows: list[dict], key: str = "sent") -> int:
    return sum(int((r.get("result") or {}).get(key, 0)) for r in rows)
