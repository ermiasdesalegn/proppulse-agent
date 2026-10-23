"""In-process event bus behind the dashboard's live activity stream (Server-Sent Events).

Publishing never blocks and never raises: a slow or vanished dashboard tab must not slow a buyer's chat down.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


class Subscription:
    def __init__(self, bus: "EventBus", backlog: list[dict]):
        self._bus = bus
        self.backlog = backlog
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        self.last_id = backlog[-1]["id"] if backlog else 0

    def close(self) -> None:
        self._bus._subscribers.discard(self)


class EventBus:
    def __init__(self, history: int = 300):
        self._subscribers: set[Subscription] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._next_id = 1

    def publish(self, kind: str, /, **data: Any) -> dict:
        # `kind` is positional-only and written last, so a payload field of the same name can never replace the event type.
        event = {**data, "id": self._next_id, "ts": datetime.now(timezone.utc).isoformat(), "kind": kind}
        self._next_id += 1
        self._history.append(event)
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(sub)  # a stalled consumer is dropped; it reconnects and replays from history
        return event

    def subscribe(self, after_id: int = 0) -> Subscription:
        """Subscribe, then replay recent history newer than after_id. Registering first means nothing is missed."""
        sub = Subscription(self, [e for e in self._history if e["id"] > after_id])
        self._subscribers.add(sub)
        return sub

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)


def format_sse(event: dict) -> str:
    return f"id: {event['id']}\nevent: {event['kind']}\ndata: {json.dumps(event, default=str)}\n\n"


async def sse_frames(bus: EventBus, *, after_id: int = 0, is_disconnected: Callable[[], Awaitable[bool]], ping_seconds: float = 15.0):
    """Server-Sent Events frames for one dashboard tab: recent history first, then live events, with a ping while idle."""
    sub = bus.subscribe(after_id)
    try:
        yield "retry: 3000\n: connected\n\n"
        for event in sub.backlog:
            yield format_sse(event)
        while not await is_disconnected():
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=ping_seconds)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # keeps proxies from closing an idle stream
                continue
            if event["id"] > sub.last_id:  # skip anything already replayed from history
                sub.last_id = event["id"]
                yield format_sse(event)
    finally:
        sub.close()


def excerpt(text: str | None, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_update(update: dict | None) -> dict:
    """What a node changed, reduced to what the inspector shows. Embeddings and full transcripts are left out."""
    u = update or {}
    out: dict[str, Any] = {k: u[k] for k in ("intent", "urgency", "query_text", "deal_stage", "booked", "transcript_failed", "human_takeover") if k in u}
    if scope := u.get("search_scope"):
        out["search_scope"] = {k: scope.get(k) for k in ("relaxation_step", "max_relaxation_steps", "effective_max_budget_etb", "effective_sub_cities")}
    if "active_matches" in u:
        out["matches"] = [{"title": m["title"], "price_etb": m["price_etb"], "similarity": m["similarity"]} for m in u["active_matches"] or []]
    if prefs := u.get("buyer_preferences"):
        out["preferences"] = prefs
    if u.get("reply"):
        out["reply"] = excerpt(u["reply"])
    if matrix := u.get("comparison_matrix"):
        out["compared"] = list(matrix)
    return out
