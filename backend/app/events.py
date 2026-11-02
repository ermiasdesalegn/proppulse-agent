"""Event bus behind the dashboard's live activity stream (Server-Sent Events).

Publishing never blocks and never raises: a slow or vanished dashboard tab must not slow a buyer's chat down.
`EventBus` keeps everything in this process (tests, a single instance). `DurableEventBus` writes every event to Postgres and every
instance reads new rows in id order (immediately after its own writes, and every `poll_seconds` for other instances' writes), so any
instance can serve any dashboard tab and history survives restarts. Polling a table needs no dedicated LISTEN connection, which a
pooled database or a cancelled task cannot be trusted to keep.

Ids are handed out when a row is inserted but a row becomes visible when it commits, so two instances writing at once can make id 8
visible before id 7. Reading "everything after the last id seen" would then skip 7 for good. So events are delivered strictly in id
order: a missing id holds the later ones back until it appears, or until `gap_grace` seconds show it never will (an insert that was
rolled back leaves an id that is never used). Everything downstream (the stream to the browser, a tab resuming with Last-Event-ID, the
dashboard's own "newer than" check) relies on that order.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.tenancy import current_tenant, resolve_org_id

log = logging.getLogger(__name__)


def _org_in_scope() -> str:
    """The organization an event belongs to: the one the publishing code is working for, else the original one."""
    return str(resolve_org_id() if current_tenant() is None else current_tenant().org_id)  # type: ignore[union-attr]


class Subscription:
    def __init__(self, bus: EventBus, backlog: list[dict]):
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
        event = {**data, "id": self._next_id, "ts": datetime.now(UTC).isoformat(), "kind": kind, "org": _org_in_scope()}
        self._next_id += 1
        self._fan_out(event)
        return event

    def _fan_out(self, event: dict) -> None:
        self._history.append(event)
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(sub)  # a stalled consumer is dropped; it reconnects and replays from history

    def subscribe(self, after_id: int = 0) -> Subscription:
        """Subscribe, then replay recent history newer than after_id. Registering first means nothing is missed."""
        sub = Subscription(self, [e for e in self._history if e["id"] > after_id])
        self._subscribers.add(sub)
        return sub

    async def open(self, after_id: int = 0) -> Subscription:
        """Like subscribe, for callers that may have to wait for the backlog (the durable bus reads it from the database)."""
        return self.subscribe(after_id)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)


class DurableEventBus(EventBus):
    """Events go to the `event_log` table and come back from it on every instance, this one included, so all instances see
    the same events in the same order with the same ids. `publish` still never blocks or raises."""

    def __init__(self, pool: Any, *, history: int = 300, outbox: int = 2000, retention_days: int = 2, poll_seconds: float = 0.5,
                 gap_grace: float = 3.0):
        super().__init__(history)
        self._pool = pool
        self._outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=outbox)
        self._retention_days, self._poll_seconds, self._gap_grace = retention_days, poll_seconds, gap_grace
        self._wake = asyncio.Event()
        self._last_seen = 0  # every id up to this one was delivered, or given up on after gap_grace
        self._missing_since: dict[int, float] = {}  # an id not visible yet, and when it was first found missing
        self._tasks: list[asyncio.Task] = []
        self._writes = 0
        self.dropped = 0

    # ---- publishing ----

    def publish(self, kind: str, /, **data: Any) -> dict:
        event = {**data, "id": 0, "ts": datetime.now(UTC).isoformat(), "kind": kind, "org": _org_in_scope()}  # the real id is the database's
        try:
            self._outbox.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1  # a database that cannot keep up costs dashboard events, never a buyer's reply
        return event

    async def _writer(self) -> None:
        while True:
            event = await self._outbox.get()
            payload = {k: v for k, v in event.items() if k not in ("id", "ts", "kind", "org")}
            try:
                async with self._pool.connection() as conn:
                    await conn.execute(
                        "insert into event_log (kind, data, org_id) values (%s, %s::jsonb, %s)",
                        (event["kind"], json.dumps(payload, default=str), event["org"]),
                    )
                self._wake.set()  # this instance's own tabs hear about it at once; other instances on their next poll
                self._writes += 1
                if self._writes % 500 == 0:
                    await self._prune()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.dropped += 1
                log.warning("event could not be stored (%s)", type(exc).__name__)

    async def _prune(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("delete from event_log where created_at < now() - make_interval(days => %s)", (self._retention_days,))

    # ---- receiving ----

    @staticmethod
    def _row_to_event(row: dict) -> dict:
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
        return {**data, "id": int(row["id"]), "ts": row["created_at"].isoformat(), "kind": row["kind"], "org": str(row["org_id"])}

    async def _fetch_after(self, after_id: int, newest: int | None = None, *, upto: int | None = None) -> list[dict]:
        """Events after `after_id` in id order; with `upto`, none beyond it; with `newest`, only the last that many."""
        bound = "" if upto is None else " and id <= %s"
        extra: tuple = () if upto is None else (upto,)
        if newest:
            sql = ("select * from (select id, kind, data, created_at, org_id from event_log where id > %s" + bound +
                   " order by id desc limit %s) t order by id")
            params: tuple = (after_id, *extra, newest)
        else:
            sql, params = "select id, kind, data, created_at, org_id from event_log where id > %s" + bound + " order by id", (after_id, *extra)
        async with self._pool.connection() as conn:
            return [self._row_to_event(r) for r in await (await conn.execute(sql, params)).fetchall()]

    async def _deliver_new(self) -> None:
        now = asyncio.get_running_loop().time()
        expected = self._last_seen + 1
        for event in await self._fetch_after(self._last_seen):
            while expected < event["id"]:  # an id before this event is not visible yet
                first = self._missing_since.setdefault(expected, now)
                if now - first < self._gap_grace:
                    return  # hold this event and the ones after it; the next poll looks again
                self._missing_since.pop(expected, None)  # never committed: give up on it
                self._last_seen = expected
                expected += 1
            self._missing_since.pop(event["id"], None)
            self._last_seen = event["id"]
            expected = event["id"] + 1
            self._fan_out(event)

    async def _poller(self) -> None:
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._poll_seconds)
            self._wake.clear()
            try:
                await self._deliver_new()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("events could not be read (%s)", type(exc).__name__)
                await asyncio.sleep(1.0)

    # ---- lifecycle and subscribing ----

    async def start(self) -> None:
        recent = await self._fetch_after(0, self._history.maxlen)
        self._history.extend(recent)
        self._last_seen = recent[-1]["id"] if recent else 0
        # Start after the last id handed out, not the last row kept: ids taken before this start (pruned rows, failed inserts) are not
        # gaps to wait for. (An insert in flight at this very moment is the one event this instance may not show.)
        async with self._pool.connection() as conn:
            cur = await conn.execute("select greatest(coalesce(max(id), 0), coalesce(pg_sequence_last_value(pg_get_serial_sequence('event_log', 'id')::regclass), 0)) as m "
                                     "from event_log")
            self._last_seen = max(self._last_seen, int((await cur.fetchone())["m"]))
        self._tasks = [asyncio.create_task(self._writer(), name="events-writer"), asyncio.create_task(self._poller(), name="events-poller")]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def open(self, after_id: int = 0) -> Subscription:
        sub = Subscription(self, [])
        self._subscribers.add(sub)  # register first, so nothing arriving during the read is missed
        try:
            sub.backlog = await self._fetch_after(after_id, self._history.maxlen, upto=self._last_seen)  # never past an open gap
        except Exception:
            self._subscribers.discard(sub)
            raise
        sub.last_id = sub.backlog[-1]["id"] if sub.backlog else after_id
        return sub


def format_sse(event: dict) -> str:
    return f"id: {event['id']}\nevent: {event['kind']}\ndata: {json.dumps(event, default=str)}\n\n"


async def sse_frames(
    bus: EventBus, *, after_id: int = 0, is_disconnected: Callable[[], Awaitable[bool]], ping_seconds: float = 15.0, org: str | None = None
) -> AsyncIterator[str]:
    """Server-Sent Events frames for one dashboard tab: recent history first, then live events, with a ping while idle.

    With `org`, only that organization's events are sent: the bus carries everyone's, a tab must see only its own agency's."""
    sub = await bus.open(after_id)
    try:
        yield "retry: 3000\n: connected\n\n"
        for event in sub.backlog:
            if org is None or event.get("org") == org:
                yield format_sse(event)
        while not await is_disconnected():
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=ping_seconds)
            except TimeoutError:
                yield ": ping\n\n"  # keeps proxies from closing an idle stream
                continue
            if event["id"] > sub.last_id:  # skip anything already replayed from history
                sub.last_id = event["id"]
                if org is None or event.get("org") == org:
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
