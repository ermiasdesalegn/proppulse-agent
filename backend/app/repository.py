"""All SQL used by the agent and API lives here. Rows come back as plain dicts."""
from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import sql
from pydantic import BaseModel

from app.finance import rank_by_reliability
from app.tenancy import APP_ROLE, resolve_org_id, tenant_connection

LEDGER_STATUSES = (
    "LEAD_REGISTERED", "TOUR_COMPLETED", "CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID", "CLOSED_LOST",
)
# Moving a deal forward carries the buyer's own stage with it; losing one deal does not (the buyer may still sign another).
BUYER_STAGE_FOLLOWS = {"TOUR_COMPLETED", "CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID"}

BUYER_FIELDS = {
    "full_name", "phone", "email", "max_budget_etb", "min_bedrooms",
    "preferred_sub_cities", "lead_score", "deal_stage", "score_breakdown",
}
LEDGER_FIELDS = {
    "contract_value_etb", "expected_commission_etb", "next_buyer_milestone_date",
    "lead_reg_email_id", "lead_registered_at",
}


class PropertyMatch(BaseModel):
    id: UUID
    developer_id: UUID
    title: str
    sub_city: str
    price_etb: float
    bedrooms: int
    similarity: float


@dataclass(frozen=True)
class LedgerEntry:
    property_id: UUID | str
    lead_reg_email_id: str | None = None


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in values) + "]"


class Repository:
    def __init__(self, pool: Any, org_id: UUID | str | None = None, *, strict: bool = False, role: str | None = APP_ROLE):
        self._pool = pool
        self._org_id = org_id  # pins this repository to one organization (a job working for one agency); else the one in scope
        self._strict = strict
        self._role = role  # the database role the work runs as; None means the connection's own (owner) role

    @property
    def pool(self) -> Any:
        """The connections this repository uses; the job queue (platform tables, app/jobs.py) shares them."""
        return self._pool

    def as_platform(self) -> Repository:
        """The same repository running as the table owner, outside row-level security. For operator tools only (the demo
        reset, migrations-style maintenance), never for work done on behalf of an organization."""
        clone = copy.copy(self)  # a copy, not a new instance, so subclasses with their own constructors keep working
        clone._role = None
        return clone

    def _shared(self) -> Repository:
        """For writes to the shared market tables (listings, projects, developers, crawl runs). A tenant's role may only read
        them (migration 025), so these statements run as the platform. The caller has already checked who may do this."""
        return self.as_platform()

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[Any]:
        """A connection whose transaction is labelled with the organization this work belongs to."""
        async with tenant_connection(self._pool, resolve_org_id(self._org_id, strict=self._strict), self._role) as conn:
            yield conn

    async def _fetch(self, query: str | sql.Composable, params: Sequence[Any] = ()) -> list[dict]:
        async with self._conn() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    async def _guarded(self, query: str | sql.Composable, params: Sequence[Any] = ()) -> list[dict]:
        """Like `_fetch`, for statements that may fail on purpose (a duplicate, a rule in a trigger): the failure is undone
        on its own, so the caller's transaction stays usable."""
        async with self._conn() as conn:
            async with conn.transaction():
                cur = await conn.execute(query, params)
                return await cur.fetchall()

    async def _one(self, query: str | sql.Composable, params: Sequence[Any] = ()) -> dict | None:
        rows = await self._fetch(query, params)
        return rows[0] if rows else None

    # ---- properties & developers ----

    async def search_properties(
        self,
        embedding: Sequence[float],
        *,
        max_budget_etb: float | None,
        min_bedrooms: int | None,
        sub_cities: Sequence[str] | None,
        threshold: float,
        count: int = 3,
    ) -> list[PropertyMatch]:
        # Ask for a few more than needed so that, among near-equal matches, the more reliable developer can come first.
        rows = await self._fetch(
            "select * from match_properties_hybrid(%s::vector, %s, %s, %s::text[], %s, %s)",
            (vector_literal(embedding), max_budget_etb, min_bedrooms, list(sub_cities or []), count * 3, threshold),
        )
        matches = [PropertyMatch(**r) for r in rows]
        if len(matches) > 1:
            scores = await self._fetch("select id, reliability_score from developers where id = any(%s::uuid[])", ([str(m.developer_id) for m in matches],))
            reliability = {str(r["id"]): float(r["reliability_score"]) for r in scores if r["reliability_score"] is not None}
            matches = rank_by_reliability(matches, reliability)
        return matches[:count]

    RANKINGS = {  # a fixed list: the column and direction are never taken from text
        "cheapest": "price_etb asc", "priciest": "price_etb desc", "highest_yield": "est_rental_yield_pct desc nulls last, price_etb",
        "largest": "sqm desc, price_etb",
    }

    async def rank_properties(
        self, ranking: str, *, max_budget_etb: float | None, min_bedrooms: int | None, sub_cities: Sequence[str] | None, count: int = 3,
    ) -> list[PropertyMatch]:
        """The available units that fit the buyer's limits, ordered by a rule. Similarity is not part of it: a unit is 'the cheapest'
        whatever its description says, so it is reported as an exact match (1.0)."""
        order = self.RANKINGS[ranking]
        rows = await self._fetch(
            "select id, developer_id, title, sub_city, price_etb, bedrooms, 1.0::float8 as similarity from properties "
            "where status = 'AVAILABLE' and (%s::numeric is null or price_etb <= %s) and (%s::int is null or bedrooms >= %s) "
            "and (cardinality(%s::text[]) = 0 or sub_city = any(%s::text[])) "
            f"order by {order} limit %s",
            (max_budget_etb, max_budget_etb, min_bedrooms, min_bedrooms, list(sub_cities or []), list(sub_cities or []), count),
        )
        return [PropertyMatch(**r) for r in rows]

    async def get_properties(self, ids: Sequence[UUID | str]) -> list[dict]:
        """Full property rows (no embedding) joined with developer contact details, in the order requested."""
        rows = await self._fetch(
            "select p.id, p.developer_id, p.title, p.project_name, p.unit_type, p.sub_city, p.lat, p.lng, p.price_etb, "
            "p.bedrooms, p.sqm, p.down_payment_pct, p.construction_stage_pct, p.est_rental_yield_pct, p.delivery_date, "
            "p.status, p.description, p.floor_plan_image_url, p.vision_amenities, p.payment_plan, p.source_url, "
            "d.name as developer_name, d.slug as developer_slug, d.sales_email, d.commission_rate_pct, "
            "d.payment_plan as developer_payment_plan, d.reliability_score "
            "from properties p join developers d on d.id = p.developer_id where p.id = any(%s::uuid[])",
            ([str(i) for i in ids],),
        )
        by_id = {str(r["id"]): r for r in rows}
        return [by_id[str(i)] for i in ids if str(i) in by_id]

    async def update_property_price(self, property_id: UUID | str, new_price_etb: float) -> dict:
        """Change a unit's price and log it, so the next proactive run can see the drop."""
        async with self._shared()._conn() as conn:
            async with conn.transaction():
                cur = await conn.execute("select price_etb from properties where id = %s for update", (str(property_id),))
                row = await cur.fetchone()
                if row is None:
                    raise LookupError(f"property {property_id} not found")
                old = row["price_etb"]
                if old == Decimal(str(new_price_etb)):
                    return {"property_id": str(property_id), "old_price_etb": float(old), "new_price_etb": float(old), "change_pct": 0.0, "changed": False}
                change_pct = round((Decimal(str(new_price_etb)) - old) / old * 100, 2)
                await conn.execute("update properties set price_etb = %s where id = %s", (new_price_etb, str(property_id)))
                await conn.execute(
                    "insert into price_history (property_id, old_price_etb, new_price_etb, change_pct) values (%s, %s, %s, %s)",
                    (str(property_id), old, new_price_etb, change_pct),
                )
        return {"property_id": str(property_id), "old_price_etb": float(old), "new_price_etb": float(new_price_etb), "change_pct": float(change_pct), "changed": True}

    # ---- ingestion ----

    async def list_developers(self) -> list[dict]:
        return await self._fetch("select id, name, slug, sales_email, website_url, scrape_url, scrape_profile, fetch_mode from developers order by name")

    async def set_developer_scrape(self, slug: str, url: str | None, profile: dict | None, fetch_mode: str | None = None) -> dict:
        from psycopg.types.json import Jsonb

        row = await self._shared()._one(
            "update developers set scrape_url = %s, scrape_profile = %s, fetch_mode = coalesce(%s, fetch_mode) where slug = %s "
            "returning id, name, slug, scrape_url, scrape_profile, fetch_mode",
            (url, Jsonb(profile) if profile else None, fetch_mode, slug),
        )
        if row is None:
            raise LookupError(f"developer {slug} not found")
        return row

    async def get_property_by_hash(self, content_hash: str) -> dict | None:
        return await self._one(
            "select id, developer_id, title, price_etb, status, floor_plan_image_url, source_url, photo_url from properties where content_hash = %s", (content_hash,))

    async def insert_property(self, *, developer_id: UUID | str, unit: Any, content_hash: str, embedding: Sequence[float],
                              vision_amenities: dict, lat: float | None, lng: float | None) -> UUID | None:
        """Insert a unit; None if another run just inserted the same one (the hash is unique)."""
        from psycopg.types.json import Jsonb

        row = await self._shared()._one(
            "insert into properties (developer_id, title, project_name, unit_type, sub_city, lat, lng, price_etb, bedrooms, sqm, "
            "down_payment_pct, construction_stage_pct, est_rental_yield_pct, delivery_date, description, floor_plan_image_url, "
            "vision_amenities, content_hash, embedding, source_url, photo_url) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s) "
            "on conflict (content_hash) do nothing returning id",
            (str(developer_id), unit.display_title, unit.project_name, unit.unit_type, unit.sub_city, lat, lng, unit.price_etb, unit.bedrooms,
             unit.sqm, unit.down_payment_pct, unit.construction_stage_pct, unit.est_rental_yield_pct, unit.delivery_date, unit.description,
             unit.floor_plan_image_url, Jsonb(vision_amenities), content_hash, vector_literal(embedding), unit.source_url, unit.photo_url),
        )
        return None if row is None else row["id"]

    async def update_property_source(self, property_id: UUID | str, url: str) -> None:
        await self._shared()._fetch("update properties set source_url = %s where id = %s returning id", (url, str(property_id)))

    async def properties_missing_photo(self, *, include_existing: bool = False) -> list[dict]:
        missing = "" if include_existing else "and p.photo_url is null "
        return await self._fetch(
            "select p.id, p.source_url, p.photo_url, d.slug as developer_slug from properties p join developers d on d.id = p.developer_id "
            f"where p.source_url is not null {missing}and p.status = 'AVAILABLE' order by d.slug, p.created_at desc")

    async def update_property_photo(self, property_id: UUID | str, url: str | None) -> None:
        await self._shared()._fetch("update properties set photo_url = %s where id = %s returning id", (url, str(property_id)))

    async def update_property_image(self, property_id: UUID | str, url: str) -> None:
        await self._shared()._fetch("update properties set floor_plan_image_url = %s where id = %s returning id", (url, str(property_id)))

    async def set_property_status(self, ids: Sequence[UUID | str], status: str) -> int:
        rows = await self._shared()._fetch("update properties set status = %s where id = any(%s::uuid[]) returning id", (status, [str(i) for i in ids]))
        return len(rows)

    async def available_for_developer(self, developer_id: UUID | str) -> list[dict]:
        return await self._fetch(
            "select id, title, project_name, content_hash from properties where developer_id = %s and status = 'AVAILABLE'", (str(developer_id),))

    async def coordinates_for(self, developer_id: UUID | str, project_name: str, sub_city: str) -> tuple[float, float] | None:
        """Where a new unit probably is: another unit of the same project, else the middle of the area's existing units."""
        row = await self._one(
            "select lat, lng from properties where developer_id = %s and lower(project_name) = lower(%s) and lat is not null limit 1",
            (str(developer_id), project_name))
        if row is None:
            row = await self._one("select avg(lat) as lat, avg(lng) as lng from properties where sub_city = %s and lat is not null", (sub_city,))
        return (float(row["lat"]), float(row["lng"])) if row and row["lat"] is not None else None

    async def record_ingest_run(self, *, developer_id: UUID | str | None, source: str, dry_run: bool, full_sync: bool, counts: dict, report: dict,
                                error: str | None = None, items_found: int | None = None) -> int:
        from psycopg.types.json import Jsonb

        row = await self._shared()._one(
            "insert into ingest_runs (developer_id, source, dry_run, full_sync, created, price_changed, removed, unchanged, skipped, report, error, items_found) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (None if developer_id is None else str(developer_id), source, dry_run, full_sync, counts["created"], counts["price_changed"],
             counts["removed"], counts["unchanged"], counts["skipped"], Jsonb(report, dumps=lambda o: json.dumps(o, default=str)), error, items_found),
        )
        return row["id"]

    async def recent_page_runs(self, developer_id: UUID | str, limit: int = 10) -> list[dict]:
        """The latest real (not preview) runs that read a developer's page, newest first."""
        return await self._fetch(
            "select id, created_at, error, items_found, created, price_changed, removed, unchanged from ingest_runs "
            "where developer_id = %s and not dry_run and source in ('url', 'html') order by id desc limit %s", (str(developer_id), limit))

    async def list_ingest_runs(self, limit: int = 20) -> list[dict]:
        return await self._fetch(
            "select r.id, r.source, r.dry_run, r.full_sync, r.created, r.price_changed, r.removed, r.unchanged, r.skipped, r.created_at, "
            "d.name as developer_name from ingest_runs r left join developers d on d.id = r.developer_id order by r.id desc limit %s", (limit,))

    # ---- portal crawl runs ----

    async def queue_crawl_runs(self, *, portals: Sequence[str], request_id: str, dry_run: bool, mode: str, page_limit: int | None) -> list[dict]:
        rows = []
        for portal in portals:
            rows.append(await self._shared()._one(
                "insert into crawl_runs (portal, request_id, trigger, status, dry_run, mode, page_limit) values (%s,%s,'dashboard','QUEUED',%s,%s,%s) returning *",
                (portal, request_id, dry_run, mode, page_limit)))
        return [r for r in rows if r]

    async def start_crawl_run(self, *, portal: str, request_id: str | None, trigger: str, run_url: str | None, dry_run: bool, mode: str,
                              page_limit: int | None) -> dict:
        """The runner says it has started: claim the row the dashboard queued for it, or open a new one (a scheduled run)."""
        if request_id:
            row = await self._shared()._one(
                "update crawl_runs set status = 'RUNNING', started_at = now(), run_url = %s, dry_run = %s, mode = %s, page_limit = %s "
                "where id = (select id from crawl_runs where request_id = %s and portal = %s and status = 'QUEUED' order by id limit 1) returning *",
                (run_url, dry_run, mode, page_limit, request_id, portal))
            if row:
                return row
        row = await self._shared()._one(
            "insert into crawl_runs (portal, request_id, trigger, status, dry_run, mode, page_limit, run_url, started_at) "
            "values (%s,%s,%s,'RUNNING',%s,%s,%s,%s, now()) returning *", (portal, request_id, trigger, dry_run, mode, page_limit, run_url))
        assert row is not None
        return row

    async def finish_crawl_run(self, run_id: int, *, status: str, stats: dict, blocked_reason: str | None, error: str | None) -> dict | None:
        from psycopg.types.json import Jsonb

        return await self._shared()._one(
            "update crawl_runs set status = %s, stats = %s, blocked_reason = %s, error = %s, finished_at = now() where id = %s returning *",
            (status, Jsonb(stats), blocked_reason, error, run_id))

    async def previous_crawl_run(self, portal: str, before_id: int) -> dict | None:
        return await self._one("select * from crawl_runs where portal = %s and id < %s and status = 'FINISHED' and not dry_run order by id desc limit 1", (portal, before_id))

    async def list_crawl_runs(self, limit: int = 40) -> list[dict]:
        return await self._fetch("select * from crawl_runs order by id desc limit %s", (limit,))

    # ---- channel ads ----

    async def find_property_by_code(self, code: str) -> dict | None:
        rows = await self._fetch("select id from properties where replace(id::text, '-', '') like %s limit 2", (code.lower() + "%",))
        return rows[0] if len(rows) == 1 else None  # an ambiguous code matches nothing rather than the wrong unit

    async def similar_properties(self, property_id: UUID | str, *, threshold: float, limit: int = 2) -> list[dict]:
        return await self._fetch(
            "select p.id, 1 - (p.embedding <=> src.embedding) as similarity from properties p, properties src "
            "where src.id = %s and p.id <> src.id and p.status = 'AVAILABLE' and p.embedding is not null "
            "and 1 - (p.embedding <=> src.embedding) >= %s order by p.embedding <=> src.embedding limit %s",
            (str(property_id), threshold, limit))

    async def claim_channel_post(self, *, property_id: UUID | str, kind: str, price_etb: float, chat_id: str, text: str) -> dict | None:
        """Reserve the right to announce this event once. None if this exact announcement was already made."""
        return await self._one(
            "insert into channel_posts (property_id, kind, price_etb, chat_id, text) values (%s,%s,%s,%s,%s) "
            "on conflict (org_id, property_id, kind, price_etb) where kind <> 'MANUAL' do nothing returning *",
            (str(property_id), kind, price_etb, chat_id, text))

    async def set_channel_post_message(self, post_id: int, message_id: int) -> None:
        await self._fetch("update channel_posts set telegram_message_id = %s where id = %s returning id", (message_id, post_id))

    async def release_channel_post(self, post_id: int) -> None:
        await self._fetch("delete from channel_posts where id = %s returning id", (post_id,))

    async def list_channel_posts(self, limit: int = 20) -> list[dict]:
        return await self._fetch(
            "select c.id, c.kind, c.price_etb, c.text, c.created_at, c.telegram_message_id, p.title from channel_posts c "
            "join properties p on p.id = c.property_id order by c.id desc limit %s", (limit,))

    # ---- proactive jobs ----

    _PROPERTY_EVENT_COLUMNS = (
        "p.id as property_id, p.title, p.sub_city, p.bedrooms, p.sqm, p.price_etb, p.lat, p.lng, p.developer_id, "
        "d.name as developer_name"
    )

    async def recent_price_drops(self, since: datetime) -> list[dict]:
        return await self._fetch(
            f"select ph.id as price_history_id, ph.old_price_etb, ph.new_price_etb, ph.change_pct, ph.detected_at, "
            f"{self._PROPERTY_EVENT_COLUMNS} from price_history ph join properties p on p.id = ph.property_id "
            f"join developers d on d.id = p.developer_id "
            # Only drops still in effect: if the price moved again since, the drop is no longer true and is not news.
            f"where ph.detected_at >= %s and ph.change_pct < 0 and p.status = 'AVAILABLE' and p.price_etb = ph.new_price_etb "
            f"order by ph.detected_at",
            (since,),
        )

    async def recent_price_increases(self, since: datetime) -> list[dict]:
        return await self._fetch(
            f"select ph.id as price_history_id, ph.old_price_etb, ph.new_price_etb, ph.change_pct, ph.detected_at, "
            f"{self._PROPERTY_EVENT_COLUMNS} from price_history ph join properties p on p.id = ph.property_id "
            f"join developers d on d.id = p.developer_id "
            f"where ph.detected_at >= %s and ph.change_pct > 0 and p.status = 'AVAILABLE' and p.price_etb = ph.new_price_etb "
            f"order by ph.detected_at",
            (since,),
        )

    async def recent_new_listings(self, since: datetime) -> list[dict]:
        # Seed rows are demo inventory, not market events, so they never trigger "new launch" messages.
        return await self._fetch(
            f"select {self._PROPERTY_EVENT_COLUMNS} from properties p join developers d on d.id = p.developer_id "
            f"where p.created_at >= %s and p.status = 'AVAILABLE' and coalesce(p.vision_amenities->>'source', '') <> 'seed' "
            f"order by p.created_at",
            (since,),
        )

    async def match_buyers_for_property(self, property_id: UUID | str, *, threshold: float, limit: int = 50) -> list[dict]:
        return await self._fetch("select * from match_buyers_for_property(%s, %s, %s)", (str(property_id), threshold, limit))

    async def claim_event(
        self, *, kind: str, audience: str, dedupe_key: str, buyer_id: UUID | str | None = None,
        property_id: UUID | str | None = None, tour_id: UUID | str | None = None, ledger_id: UUID | str | None = None,
    ) -> dict | None:
        """Reserve the right to send this exact message to this exact recipient. None means it was already sent
        (or is being sent right now); a reservation that never finished is retried after ten minutes."""
        return await self._one(
            "insert into proactive_events (kind, audience, buyer_id, property_id, tour_id, ledger_id, dedupe_key) "
            "values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (org_id, dedupe_key) do update set created_at = now() "
            "where proactive_events.status = 'pending' and proactive_events.created_at < now() - interval '10 minutes' "
            "returning *",
            (kind, audience, None if buyer_id is None else str(buyer_id), None if property_id is None else str(property_id),
             None if tour_id is None else str(tour_id), None if ledger_id is None else str(ledger_id), dedupe_key),
        )

    async def finish_event(self, event_id: int, message: str, channel: str) -> None:
        await self._fetch(
            "update proactive_events set status = 'sent', message = %s, channel = %s where id = %s returning id",
            (message, channel, event_id),
        )

    async def release_event(self, event_id: int) -> None:
        await self._fetch("delete from proactive_events where id = %s and status = 'pending' returning id", (event_id,))

    async def recent_buyer_alert_count(self, buyer_id: UUID | str, since: datetime) -> int:
        row = await self._one(
            "select count(*) as n from proactive_events where buyer_id = %s and audience = 'buyer' and status = 'sent' "
            "and kind in ('PRICE_DROP', 'NEW_LISTING') and created_at >= %s",
            (str(buyer_id), since),
        )
        return int(row["n"])

    async def list_events(self, *, audience: str | None = None, limit: int = 100) -> list[dict]:
        return await self._fetch(
            "select e.*, b.full_name as buyer_name from proactive_events e left join buyers_crm b on b.id = e.buyer_id "
            "where e.status = 'sent' and (%s::text is null or e.audience = %s) order by e.id desc limit %s",
            (audience, audience, limit),
        )

    async def tours_for_reminder(self, start: datetime, end: datetime) -> list[dict]:
        return await self._fetch(
            "select t.id, t.scheduled_start, t.project_id, b.id as buyer_id, b.telegram_id, b.full_name, b.email, b.thread_id "
            "from tours_and_appointments t join buyers_crm b on b.id = t.buyer_id "
            "where t.scheduled_start > %s and t.scheduled_start <= %s and t.reminder_sent = false order by t.scheduled_start",
            (start, end),
        )

    async def ledger_for_milestones(self) -> list[dict]:
        return await self._fetch(
            "select l.id, l.next_buyer_milestone_date, l.status, l.expected_commission_etb, "
            "b.id as buyer_id, b.telegram_id, b.full_name, b.email, b.thread_id, "
            "d.name as developer_name, p.title as property_title "
            "from commission_ledger l join buyers_crm b on b.id = l.buyer_id join developers d on d.id = l.developer_id "
            "join properties p on p.id = l.property_id "
            "where l.status = 'CONTRACT_SIGNED' and l.next_buyer_milestone_date is not null order by l.next_buyer_milestone_date"
        )

    async def ledger_commission_pending(self) -> list[dict]:
        return await self._fetch(
            "select l.id, l.expected_commission_etb, b.id as buyer_id, b.full_name, d.name as developer_name, p.title as property_title "
            "from commission_ledger l join buyers_crm b on b.id = l.buyer_id join developers d on d.id = l.developer_id "
            "join properties p on p.id = l.property_id where l.status = 'COMMISSION_PENDING' order by l.updated_at"
        )

    async def get_developer_by_slug(self, slug: str) -> dict | None:
        return await self._one("select * from developers where slug = %s", (slug,))

    async def find_developer_for_sender(self, email: str) -> dict | None:
        """The developer a sender belongs to: the exact sales address, else any address at the same company domain."""
        email = email.strip().lower()
        exact = await self._one("select * from developers where lower(sales_email) = %s", (email,))
        if exact or "@" not in email:
            return exact
        domain = email.rsplit("@", 1)[1]
        rows = await self._fetch("select * from developers where lower(split_part(sales_email, '@', 2)) = %s", (domain,))
        return rows[0] if len(rows) == 1 else None  # a shared domain such as gmail.com matches nobody

    async def claim_announcement(self, message_id: str, sender: str, subject: str | None) -> bool:
        """True the first time a message id is seen; False for a redelivery, so each email is processed once."""
        rows = await self._fetch(
            "insert into inbound_announcements (message_id, sender, subject, status) values (%s, %s, %s, 'FAILED') "
            "on conflict (org_id, message_id) do nothing returning message_id",
            (message_id, sender, subject),
        )
        return bool(rows)

    async def finish_announcement(self, message_id: str, status: str, developer_id: Any = None, detail: dict | None = None) -> None:
        from psycopg.types.json import Jsonb

        await self._fetch(
            "update inbound_announcements set status = %s, developer_id = %s, detail = %s where message_id = %s returning message_id",
            (status, None if developer_id is None else str(developer_id), Jsonb(detail or {}), message_id),
        )

    async def list_announcements(self, limit: int = 30) -> list[dict]:
        return await self._fetch(
            "select a.message_id, a.sender, a.subject, a.status, a.detail, a.received_at, d.name as developer_name "
            "from inbound_announcements a left join developers d on d.id = a.developer_id order by a.received_at desc limit %s",
            (limit,),
        )

    # ---- buyers ----

    async def get_or_create_buyer(self, thread_id: str, telegram_id: int | None = None) -> dict:
        row = await self._one(
            "insert into buyers_crm (thread_id, telegram_id) values (%s, %s) "
            "on conflict (org_id, thread_id) do update set thread_id = excluded.thread_id returning *",
            (thread_id, telegram_id),
        )
        assert row is not None
        return row

    async def get_buyer(self, buyer_id: UUID | str) -> dict | None:
        return await self._one("select * from buyers_crm where id = %s", (str(buyer_id),))

    async def update_buyer(self, buyer_id: UUID | str, **fields: Any) -> dict:
        unknown = set(fields) - BUYER_FIELDS
        if unknown or not fields:
            raise ValueError(f"cannot update buyer fields: {sorted(unknown) or 'none given'}")
        from psycopg.types.json import Jsonb

        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
        values = [Jsonb(v) if k == "score_breakdown" else v for k, v in fields.items()]
        row = await self._one(
            sql.SQL("update buyers_crm set {} where id = %s returning *").format(sets),
            [*values, str(buyer_id)],
        )
        if row is None:
            raise LookupError(f"buyer {buyer_id} not found")
        return row

    async def record_score(self, buyer_id: UUID | str, score: int, gained: Sequence[dict]) -> None:
        """Remember that a buyer's score went up, and what raised it."""
        from psycopg.types.json import Jsonb

        await self._fetch("insert into lead_score_history (buyer_id, score, gained) values (%s, %s, %s) returning id", (str(buyer_id), score, Jsonb(list(gained))))

    async def score_history(self, buyer_id: UUID | str, limit: int = 100) -> list[dict]:
        return await self._fetch(
            "select score, gained, created_at from lead_score_history where buyer_id = %s order by id limit %s", (str(buyer_id), limit))

    async def get_buyer_by_thread(self, thread_id: str) -> dict | None:
        return await self._one("select * from buyers_crm where thread_id = %s", (thread_id,))

    async def list_buyers(self, limit: int = 100) -> list[dict]:
        return await self._fetch(
            "select id, thread_id, telegram_id, full_name, phone, email, max_budget_etb, min_bedrooms, preferred_sub_cities, "
            "lead_score, deal_stage, human_takeover, preference_summary, created_at, updated_at "
            "from buyers_crm order by updated_at desc limit %s",
            (limit,),
        )

    # ---- transcript ----

    async def add_message(
        self, buyer_id: UUID | str, role: str, content: str, *, source: str = "text", telegram_file_id: str | None = None,
        attachments: Sequence[dict] | None = None,
    ) -> dict:
        from psycopg.types.json import Jsonb

        row = await self._one(
            "insert into chat_messages (buyer_id, role, content, source, telegram_file_id, attachments) values (%s, %s, %s, %s, %s, %s) returning *",
            (str(buyer_id), role, content, source, telegram_file_id, Jsonb(list(attachments or []))),
        )
        assert row is not None
        return row

    async def get_buyer_message(self, buyer_id: UUID | str, message_id: int) -> dict | None:
        """One message of this buyer's transcript; None when it belongs to someone else."""
        return await self._one("select * from chat_messages where id = %s and buyer_id = %s", (message_id, str(buyer_id)))

    async def list_messages(self, buyer_id: UUID | str, *, after_id: int = 0, limit: int = 200) -> list[dict]:
        return await self._fetch(
            "select * from (select * from chat_messages where buyer_id = %s and id > %s order by id limit %s) m order by id",
            (str(buyer_id), after_id, limit),
        )

    async def update_message_content(self, message_id: int, content: str) -> None:
        await self._fetch("update chat_messages set content = %s where id = %s returning id", (content, message_id))

    async def set_human_takeover(self, buyer_id: UUID | str, on: bool) -> dict:
        row = await self._one("update buyers_crm set human_takeover = %s where id = %s returning *", (on, str(buyer_id)))
        if row is None:
            raise LookupError(f"buyer {buyer_id} not found")
        return row

    async def set_preference_embedding(self, buyer_id: UUID | str, summary: str, embedding: Sequence[float]) -> None:
        await self._fetch(
            "update buyers_crm set preference_summary = %s, preference_embedding = %s::vector where id = %s returning id",
            (summary, vector_literal(embedding), str(buyer_id)),
        )

    # ---- tours ----

    async def create_tour(
        self,
        *,
        buyer_id: UUID | str,
        scheduled_start: datetime,
        stops: Sequence[UUID | str],
        route_json: dict | None,
        calendar_event_id: str | None,
        kind: str = "SITE_VISIT",
        project_id: UUID | str | None = None,
    ) -> dict:
        """Create the tour and its ordered stops atomically. `stops` is already in visiting order."""
        from psycopg.types.json import Jsonb

        async with self._conn() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "insert into tours_and_appointments (buyer_id, scheduled_start, calendar_event_id, mapbox_route_json, kind, project_id) "
                    "values (%s, %s, %s, %s, %s, %s) returning *",
                    (str(buyer_id), scheduled_start, calendar_event_id, Jsonb(route_json) if route_json else None, kind, None if project_id is None else str(project_id)),
                )
                tour = await cur.fetchone()
                for order, property_id in enumerate(stops, start=1):
                    await conn.execute(
                        "insert into tour_properties (tour_id, property_id, stop_order) values (%s, %s, %s)",
                        (tour["id"], str(property_id), order),
                    )
        return tour

    async def get_tour(self, tour_id: UUID | str) -> dict | None:
        return await self._one("select * from tours_and_appointments where id = %s", (str(tour_id),))

    async def mark_dossier_sent(self, tour_id: UUID | str) -> None:
        await self._fetch("update tours_and_appointments set dossier_sent_at = now() where id = %s returning id", (str(tour_id),))

    async def add_objections(self, buyer_id: UUID | str, texts: Sequence[str], *, keep: int = 20) -> list[dict]:
        """Remember what the buyer hesitated about (newest last, repeats ignored), for the broker's pre-tour dossier."""
        from psycopg.types.json import Jsonb

        buyer = await self.get_buyer(buyer_id)
        if buyer is None:
            raise LookupError(f"buyer {buyer_id} not found")
        known = list(buyer.get("objections") or [])
        seen = {o["text"].strip().lower() for o in known}
        now = datetime.now(UTC).isoformat()
        for text in texts:
            text = " ".join(str(text).split())[:300]
            if text and text.lower() not in seen:
                known.append({"text": text, "at": now})
                seen.add(text.lower())
        known = known[-keep:]
        await self._fetch("update buyers_crm set objections = %s where id = %s returning id", (Jsonb(known), str(buyer_id)))
        return known

    async def get_tour_stops(self, tour_id: UUID | str) -> list[dict]:
        return await self._fetch(
            "select tp.stop_order, p.id as property_id, p.title, p.lat, p.lng, p.developer_id "
            "from tour_properties tp join properties p on p.id = tp.property_id "
            "where tp.tour_id = %s order by tp.stop_order",
            (str(tour_id),),
        )

    async def tours_starting_between(self, start: datetime, end: datetime) -> list[dict]:
        return await self._fetch(
            "select t.*, b.telegram_id, b.full_name from tours_and_appointments t join buyers_crm b on b.id = t.buyer_id "
            "where t.scheduled_start >= %s and t.scheduled_start < %s order by t.scheduled_start",
            (start, end),
        )

    async def mark_reminder_sent(self, tour_id: UUID | str) -> None:
        await self._fetch("update tours_and_appointments set reminder_sent = true where id = %s returning id", (str(tour_id),))

    # ---- commission ledger ----

    async def create_ledger_rows(self, *, buyer_id: UUID | str, tour_id: UUID | str | None, entries: Sequence[LedgerEntry]) -> list[dict]:
        """One row per entry. Developer and expected commission are derived from the property."""
        rows: list[dict] = []
        async with self._conn() as conn:
            async with conn.transaction():
                for e in entries:
                    cur = await conn.execute(
                        "insert into commission_ledger (buyer_id, developer_id, property_id, tour_id, lead_reg_email_id, "
                        "lead_registered_at, expected_commission_etb) "
                        "select %s, p.developer_id, p.id, %s, %s, case when %s::text is null then null else now() end, "
                        "round(p.price_etb * d.commission_rate_pct / 100, 2) "
                        "from properties p join developers d on d.id = p.developer_id where p.id = %s returning *",
                        (str(buyer_id), str(tour_id) if tour_id else None, e.lead_reg_email_id, e.lead_reg_email_id, str(e.property_id)),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise LookupError(f"property {e.property_id} not found")
                    rows.append(row)
        return rows

    async def update_ledger(self, ledger_id: UUID | str, *, status: str | None = None, **fields: Any) -> dict:
        unknown = set(fields) - LEDGER_FIELDS
        if unknown:
            raise ValueError(f"cannot update ledger fields: {sorted(unknown)}")
        if status is not None:
            fields["status"] = status
        if not fields:
            raise ValueError("nothing to update")
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
        row = await self._one(
            sql.SQL("update commission_ledger set {} where id = %s returning *").format(sets),
            [*fields.values(), str(ledger_id)],
        )
        if row is None:
            raise LookupError(f"ledger row {ledger_id} not found")
        return row

    async def list_ledger(self, status: str | None = None) -> list[dict]:
        return await self._fetch(
            "select l.*, b.full_name as buyer_name, b.lead_score, d.name as developer_name, d.slug as developer_slug, "
            "p.title as property_title, p.price_etb as property_price_etb "
            "from commission_ledger l join buyers_crm b on b.id = l.buyer_id "
            "join developers d on d.id = l.developer_id join properties p on p.id = l.property_id "
            "where (%s::text is null or l.status = %s) order by l.created_at desc",
            (status, status),
        )

    async def move_deal(
        self, ledger_id: UUID | str, status: str, *, contract_value_etb: float | None = None,
        next_buyer_milestone_date: date | None = None,
    ) -> dict:
        """Move a deal to another stage and keep the buyer's own stage in step. Signing needs a payment milestone date,
        because the reminder job runs on it."""
        if status not in LEDGER_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        async with self._conn() as conn:
            async with conn.transaction():
                cur = await conn.execute("select * from commission_ledger where id = %s for update", (str(ledger_id),))
                row = await cur.fetchone()
                if row is None:
                    raise LookupError(f"ledger row {ledger_id} not found")
                milestone = next_buyer_milestone_date or row["next_buyer_milestone_date"]
                if status == "CONTRACT_SIGNED" and milestone is None:
                    raise ValueError("a payment milestone date is required when a contract is signed")
                cur = await conn.execute(
                    "update commission_ledger set status = %s, contract_value_etb = coalesce(%s, contract_value_etb), "
                    "next_buyer_milestone_date = %s where id = %s returning *",
                    (status, contract_value_etb, milestone, str(ledger_id)),
                )
                updated = await cur.fetchone()
                if status in BUYER_STAGE_FOLLOWS:
                    await conn.execute("update buyers_crm set deal_stage = %s where id = %s", (status, row["buyer_id"]))
        return updated

    async def board(self) -> dict:
        """Everything the deal board shows: qualified buyers with no deal yet, and every deal row."""
        qualified = await self._fetch(
            "select b.id, b.full_name, b.thread_id, b.max_budget_etb, b.min_bedrooms, b.preferred_sub_cities, b.lead_score, "
            "b.deal_stage, b.human_takeover from buyers_crm b "
            "where b.deal_stage in ('QUALIFIED', 'INVENTORY_PENDING') "
            "and not exists (select 1 from commission_ledger l where l.buyer_id = b.id) order by b.lead_score desc, b.updated_at desc"
        )
        return {"qualified": qualified, "deals": await self.list_ledger()}

    async def list_properties_for_map(self) -> list[dict]:
        return await self._fetch(
            "select p.id, p.title, p.project_name, p.unit_type, p.sub_city, p.lat, p.lng, p.price_etb, p.bedrooms, p.sqm, p.status, "
            "d.name as developer_name, d.slug as developer_slug from properties p join developers d on d.id = p.developer_id "
            "order by d.slug, p.price_etb"
        )

    # ---- the buyer-facing website ----
    # Only fields a buyer may see: no developer contact, commission rate, reliability score, content hash or embedding.

    _PORTAL_UNIT = (
        "select p.id, p.title, p.project_name, p.unit_type, p.sub_city, p.lat, p.lng, p.price_etb, p.bedrooms, p.sqm, p.delivery_date, "
        "p.construction_stage_pct, p.down_payment_pct, p.est_rental_yield_pct, p.status, p.created_at, "
        "d.name as developer_name, d.slug as developer_slug, "
        "p.photo_url, c.project_id as cover_project_id, c.media_id as cover_media_id, c.image_url as cover_image_url, "
        "exists (select 1 from unit_layouts l where l.property_id = p.id) as has_layout "
        "{extra}"
        "from properties p join developers d on d.id = p.developer_id "
        "left join lateral (select pr.id as project_id, pr.image_url, "
        "(select m.id from project_media m where m.project_id = pr.id and m.kind = 'photo' order by m.id desc limit 1) as media_id "
        "from projects pr where pr.developer_id = p.developer_id and lower(pr.name) = lower(p.project_name) limit 1) c on true "
    )
    _PORTAL_SORTS = {
        "newest": "p.created_at desc, p.id",
        "price_asc": "p.price_etb asc, p.id",
        "price_desc": "p.price_etb desc, p.id",
        "size_desc": "p.sqm desc, p.id",
    }

    async def portal_units(self, *, q: str | None = None, sub_city: str | None = None, min_bedrooms: int | None = None, price_min: float | None = None,
                           price_max: float | None = None, developer: str | None = None, sort: str = "newest", limit: int = 24, offset: int = 0) -> tuple[list[dict], int]:
        where, params = ["p.status = 'AVAILABLE'"], []
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(p.title ilike %s or p.project_name ilike %s or p.sub_city ilike %s or d.name ilike %s)")
            params += [like] * 4
        if sub_city:
            where.append("lower(p.sub_city) = lower(%s)")
            params.append(sub_city)
        if min_bedrooms is not None:
            where.append("p.bedrooms >= %s")
            params.append(min_bedrooms)
        if price_min is not None:
            where.append("p.price_etb >= %s")
            params.append(price_min)
        if price_max is not None:
            where.append("p.price_etb <= %s")
            params.append(price_max)
        if developer:
            where.append("d.slug = %s")
            params.append(developer)
        clause = " where " + " and ".join(where)
        order = self._PORTAL_SORTS.get(sort, self._PORTAL_SORTS["newest"])
        rows = await self._fetch(self._PORTAL_UNIT.format(extra="") + clause + f" order by {order} limit %s offset %s", (*params, limit, offset))
        total = await self._one("select count(*) as n from properties p join developers d on d.id = p.developer_id" + clause, tuple(params))
        return rows, int(total["n"]) if total else 0

    async def portal_facets(self) -> dict:
        row = await self._one(
            "select coalesce(min(price_etb), 0) as price_min, coalesce(max(price_etb), 0) as price_max, coalesce(max(bedrooms), 0) as bedrooms_max, count(*) as units "
            "from properties where status = 'AVAILABLE'"
        )
        assert row is not None
        areas = await self._fetch("select sub_city, count(*) as units from properties where status = 'AVAILABLE' group by sub_city order by count(*) desc, sub_city")
        developers = await self._fetch(
            "select d.slug, d.name, count(*) as units from properties p join developers d on d.id = p.developer_id where p.status = 'AVAILABLE' group by d.slug, d.name order by d.name"
        )
        return {**row, "sub_cities": areas, "developers": developers}

    async def portal_featured(self, limit: int = 6) -> list[dict]:
        """A spread across developers: each one's newest unit first, then their next, so one developer cannot fill the page.
        Units with a photo of their own (or one the broker uploaded for the project) fill the page first: a drawing only when too
        few have one. A project's scraped picture does not count: it is often the developer's logo."""
        return await self._fetch(
            "select * from (" + self._PORTAL_UNIT.format(
                extra=", row_number() over (partition by p.developer_id order by (p.photo_url is null), p.created_at desc, p.id) as rn ")
            + "where p.status = 'AVAILABLE') t order by (coalesce(photo_url, cover_media_id::text) is null), rn, created_at desc limit %s",
            (limit,),
        )

    async def portal_unit(self, property_id: UUID | str) -> dict | None:
        return await self._one(
            self._PORTAL_UNIT.format(extra=", p.description, p.payment_plan, p.vision_amenities, p.source_url, p.floor_plan_image_url ") + "where p.id = %s",
            (str(property_id),),
        )

    async def portal_cover(self, project_id: UUID | str) -> dict | None:
        return await self._one(
            "select m.mime, m.data from project_media m where m.project_id = %s and m.kind = 'photo' order by m.id desc limit 1", (str(project_id),)
        )

    async def portal_saved(self, user_id: str) -> list[dict]:
        return await self._fetch(
            "select * from (" + self._PORTAL_UNIT.format(extra=", s.created_at as saved_at ").replace("left join lateral", "join saved_homes s on s.property_id = p.id and s.user_id = %s left join lateral", 1)
            + "where true) t order by saved_at desc",
            (user_id,),
        )

    async def saved_ids(self, user_id: str) -> list[str]:
        return [str(r["property_id"]) for r in await self._fetch("select property_id from saved_homes where user_id = %s order by created_at desc", (user_id,))]

    async def save_home(self, user_id: str, property_id: UUID | str) -> bool:
        rows = await self._fetch(
            "insert into saved_homes (user_id, property_id, created_at) select %s, id, clock_timestamp() from properties where id = %s on conflict do nothing returning property_id", (user_id, str(property_id))
        )
        return bool(rows) or str(property_id) in await self.saved_ids(user_id)

    async def unsave_home(self, user_id: str, property_id: UUID | str) -> None:
        await self._fetch("delete from saved_homes where user_id = %s and property_id = %s returning property_id", (user_id, str(property_id)))

    async def list_tours(self, limit: int = 100) -> list[dict]:
        return await self._fetch(
            "select t.id, t.scheduled_start, t.kind, t.mapbox_route_json, t.reminder_sent, t.project_id, pj.name as project_name, b.id as buyer_id, b.full_name, b.thread_id, "
            "(select coalesce(json_agg(json_build_object('stop_order', tp.stop_order, 'property_id', p.id, 'title', p.title, "
            "'lat', p.lat, 'lng', p.lng, 'sub_city', p.sub_city) order by tp.stop_order), '[]'::json) "
            "from tour_properties tp join properties p on p.id = tp.property_id where tp.tour_id = t.id) as stops "
            "from tours_and_appointments t join buyers_crm b on b.id = t.buyer_id left join projects pj on pj.id = t.project_id order by t.scheduled_start desc limit %s",
            (limit,),
        )

    async def get_message(self, message_id: int) -> dict | None:
        return await self._one("select * from chat_messages where id = %s", (message_id,))

    async def stats(self, since: datetime) -> dict:
        row = await self._one(
            "select (select count(*) from buyers_crm) as buyers, "
            "(select count(*) from buyers_crm where human_takeover) as in_takeover, "
            "(select count(*) from buyers_crm where deal_stage in ('QUALIFIED', 'INVENTORY_PENDING')) as qualified, "
            "(select count(*) from commission_ledger where status in ('LEAD_REGISTERED', 'TOUR_COMPLETED', 'CONTRACT_SIGNED', 'COMMISSION_PENDING')) as open_deals, "
            "(select coalesce(sum(expected_commission_etb), 0) from commission_ledger where status in ('LEAD_REGISTERED', 'TOUR_COMPLETED', 'CONTRACT_SIGNED', 'COMMISSION_PENDING')) as pipeline_commission_etb, "
            "(select coalesce(sum(expected_commission_etb), 0) from commission_ledger where status = 'COMMISSION_PENDING') as commission_pending_etb, "
            "(select count(*) from tours_and_appointments where scheduled_start > now()) as upcoming_tours, "
            "(select count(*) from proactive_events where status = 'sent' and created_at >= %s) as proactive_sent",
            (since,),
        )
        assert row is not None
        return row

    async def trends(self, days: int = 30) -> dict:
        """This period against the one before it, counted from when each record was created, so the dashboard can show real change figures."""
        def pair(table: str, expr: str = "count(*)", extra: str = "") -> str:
            cur = f"(select {expr} from {table} where created_at >= now() - make_interval(days => %(d)s){extra})"
            prev = f"(select {expr} from {table} where created_at >= now() - make_interval(days => %(d)s * 2) and created_at < now() - make_interval(days => %(d)s){extra})"
            return f"{cur} as {{k}}_current, {prev} as {{k}}_previous"
        parts = {
            "buyers": pair("buyers_crm"),
            "open_deals": pair("commission_ledger", extra=" and status in ('LEAD_REGISTERED', 'TOUR_COMPLETED', 'CONTRACT_SIGNED', 'COMMISSION_PENDING')"),
            "pipeline_commission_etb": pair("commission_ledger", "coalesce(sum(expected_commission_etb), 0)", " and status in ('LEAD_REGISTERED', 'TOUR_COMPLETED', 'CONTRACT_SIGNED', 'COMMISSION_PENDING')"),
            "upcoming_tours": pair("tours_and_appointments"),
            "proactive_sent": pair("proactive_events", extra=" and status = 'sent'"),
        }
        sql = "select " + ", ".join(sql.format(k=k) for k, sql in parts.items())
        row = await self._one(sql, {"d": days})
        assert row is not None
        return {k: {"current": float(row[f"{k}_current"]), "previous": float(row[f"{k}_previous"])} for k in parts}

    # ---- projects: developers that publish no unit prices ----

    _PROJECT_COLUMNS = (
        "pr.id, pr.developer_id, pr.name, pr.slug, pr.source_url, pr.sub_city, pr.location_text, pr.summary, pr.bedrooms_min, pr.bedrooms_max, "
        "pr.sqm_min, pr.sqm_max, pr.floors, pr.status, pr.price_from_etb, pr.price_note, pr.image_url, pr.promo_text, pr.promo_discount_pct, "
        "pr.promo_until, pr.content_hash, pr.last_seen_at, d.name as developer_name, d.slug as developer_slug, d.sales_email"
    )

    async def upsert_project(self, developer_id: UUID | str, data: dict) -> tuple[dict, str]:
        """Insert or refresh one project. Returns the row and what happened: created, updated or unchanged."""
        existing = await self._shared()._one("select id, content_hash from projects where developer_id = %s and slug = %s", (str(developer_id), data["slug"]))
        values = (data["name"], data["source_url"], data.get("sub_city"), data.get("location_text"), data.get("summary"), data.get("bedrooms_min"),
                  data.get("bedrooms_max"), data.get("sqm_min"), data.get("sqm_max"), data.get("floors"), data.get("status", "SELLING"),
                  data.get("image_url"), data.get("promo_text"), data.get("promo_discount_pct"), data.get("promo_until"), data["content_hash"])
        if existing is None:
            row = await self._shared()._one(
                "insert into projects (developer_id, slug, name, source_url, sub_city, location_text, summary, bedrooms_min, bedrooms_max, sqm_min, "
                "sqm_max, floors, status, image_url, promo_text, promo_discount_pct, promo_until, content_hash) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
                (str(developer_id), data["slug"], *values))
            assert row is not None
            return row, "created"
        if existing["content_hash"] == data["content_hash"]:
            await self._shared()._fetch("update projects set last_seen_at = now() where id = %s returning id", (existing["id"],))
            return existing, "unchanged"
        row = await self._shared()._one(
            "update projects set name = %s, source_url = %s, sub_city = %s, location_text = %s, summary = %s, bedrooms_min = %s, bedrooms_max = %s, "
            "sqm_min = %s, sqm_max = %s, floors = %s, status = %s, image_url = %s, promo_text = %s, promo_discount_pct = %s, promo_until = %s, "
            "content_hash = %s, last_seen_at = now() where id = %s returning *", (*values, existing["id"]))
        assert row is not None
        return row, "updated"

    async def get_project(self, project_id: UUID | str) -> dict | None:
        return await self._one(f"select {self._PROJECT_COLUMNS} from projects pr join developers d on d.id = pr.developer_id where pr.id = %s", (str(project_id),))

    async def find_project_by_code(self, code: str) -> dict | None:
        rows = await self._fetch("select id from projects where replace(id::text, '-', '') like %s limit 2", (code.lower() + "%",))
        return rows[0] if len(rows) == 1 else None

    async def list_projects(self, *, only_selling: bool = False) -> list[dict]:
        where = "where pr.status = 'SELLING' " if only_selling else ""
        return await self._fetch(
            f"select {self._PROJECT_COLUMNS}, (select count(*) from price_requests q where q.project_id = pr.id and q.status = 'OPEN') as open_requests, "
            f"(select count(*) from project_media m where m.project_id = pr.id) as media_count, "
            f"(select m.id from project_media m where m.project_id = pr.id order by m.id desc limit 1) as cover_media_id, "
            f"(select m.kind from project_media m where m.project_id = pr.id order by m.id desc limit 1) as cover_media_kind "
            f"from projects pr join developers d on d.id = pr.developer_id {where}order by d.name, pr.name")

    async def search_projects(self, *, sub_cities: Sequence[str] | None = None, min_bedrooms: int | None = None, limit: int = 2,
                              embedding: Sequence[float] | None = None, threshold: float = 0.65) -> list[dict]:
        """Projects still selling that fit the buyer. A project qualifies if it is in one of the wanted areas, or (when the buyer's wish has a
        vector) its description is close enough in meaning. An empty area list means anywhere. Live offers first, then the closest in meaning."""
        areas = list(sub_cities or [])
        vector = vector_literal(embedding) if embedding is not None else None
        return await self._fetch(
            f"select {self._PROJECT_COLUMNS}, case when %s::vector is null or pr.embedding is null then null else 1 - (pr.embedding <=> %s::vector) end as similarity "
            "from projects pr join developers d on d.id = pr.developer_id "
            "where pr.status = 'SELLING' and (%s::int is null or pr.bedrooms_max is null or pr.bedrooms_max >= %s::int) "
            "and (cardinality(%s::text[]) = 0 or pr.sub_city = any(%s::text[]) "
            "     or (%s::vector is not null and pr.embedding is not null and 1 - (pr.embedding <=> %s::vector) >= %s)) "
            "order by (pr.promo_text is not null and (pr.promo_until is null or pr.promo_until >= current_date)) desc, "
            "(pr.sub_city = any(%s::text[])) desc nulls last, similarity desc nulls last, pr.name limit %s",
            (vector, vector, min_bedrooms, min_bedrooms, areas, areas, vector, vector, threshold, areas, limit))

    async def set_project_embedding(self, project_id: UUID | str, embedding: Sequence[float]) -> None:
        await self._shared()._fetch("update projects set embedding = %s::vector where id = %s returning id", (vector_literal(embedding), str(project_id)))

    async def set_project_price(self, project_id: UUID | str, price_from_etb: float | None, note: str | None) -> dict | None:
        return await self._shared()._one("update projects set price_from_etb = %s, price_note = %s where id = %s returning *", (price_from_etb, note, str(project_id)))

    async def set_project_promo(self, project_id: UUID | str, text: str | None, discount_pct: float | None, until: Any) -> dict | None:
        return await self._shared()._one("update projects set promo_text = %s, promo_discount_pct = %s, promo_until = %s where id = %s returning *",
                               (text, discount_pct, until, str(project_id)))

    async def create_price_request(self, project_id: UUID | str, buyer_id: UUID | str) -> tuple[dict, bool]:
        """One open request per buyer and project. Returns the request and whether it is new (or was reopened)."""
        row = await self._one("insert into price_requests (project_id, buyer_id) values (%s, %s) on conflict (project_id, buyer_id) do nothing returning *",
                              (str(project_id), str(buyer_id)))
        if row is not None:
            return row, True
        again = await self._one("select * from price_requests where project_id = %s and buyer_id = %s", (str(project_id), str(buyer_id)))
        assert again is not None
        if again["status"] == "ANSWERED":  # asked before and was told; asking again reopens it
            reopened = await self._one("update price_requests set status = 'OPEN', answered_at = null where id = %s returning *", (again["id"],))
            return reopened or again, True
        return again, False

    async def open_price_requests(self, project_id: UUID | str) -> list[dict]:
        return await self._fetch(
            "select q.id, q.buyer_id, q.created_at, b.full_name, b.telegram_id, b.thread_id, b.email, b.human_takeover from price_requests q "
            "join buyers_crm b on b.id = q.buyer_id where q.project_id = %s and q.status = 'OPEN' order by q.id", (str(project_id),))

    async def answer_price_request(self, request_id: int) -> None:
        await self._fetch("update price_requests set status = 'ANSWERED', answered_at = now() where id = %s returning id", (request_id,))

    async def list_price_requests(self, limit: int = 100) -> list[dict]:
        return await self._fetch(
            "select q.id, q.status, q.created_at, q.answered_at, pr.name as project_name, b.full_name as buyer_name, b.thread_id from price_requests q "
            "join projects pr on pr.id = q.project_id join buyers_crm b on b.id = q.buyer_id order by q.id desc limit %s", (limit,))

    async def projects_with_live_promotion(self) -> list[dict]:
        return await self._fetch(
            f"select {self._PROJECT_COLUMNS} from projects pr join developers d on d.id = pr.developer_id "
            "where pr.status = 'SELLING' and pr.promo_text is not null and (pr.promo_until is null or pr.promo_until >= current_date) order by pr.name")

    async def claim_project_post(self, *, project_id: UUID | str, kind: str, promo_key: str, chat_id: str, text: str) -> dict | None:
        return await self._one(
            "insert into project_posts (project_id, kind, promo_key, chat_id, text) values (%s, %s, %s, %s, %s) "
            "on conflict (org_id, project_id, promo_key) where kind = 'PROMO' do nothing returning *", (str(project_id), kind, promo_key, chat_id, text))

    async def set_project_post_message(self, post_id: int, message_id: int) -> None:
        await self._fetch("update project_posts set telegram_message_id = %s where id = %s returning id", (message_id, post_id))

    async def release_project_post(self, post_id: int) -> None:
        await self._fetch("delete from project_posts where id = %s returning id", (post_id,))

    async def create_developer(self, *, name: str, slug: str, sales_email: str, website_url: str | None, commission_rate_pct: float = 3.0) -> dict | None:
        """None if the slug is taken."""
        return await self._shared()._one(
            "insert into developers (name, slug, sales_email, commission_rate_pct, website_url) values (%s, %s, %s, %s, %s) "
            "on conflict (slug) do nothing returning id, name, slug, sales_email, website_url", (name, slug, sales_email, commission_rate_pct, website_url))

    # ---- project pictures and videos ----

    async def add_project_media(self, project_id: UUID | str, *, kind: str, mime: str, filename: str | None, data: bytes) -> dict:
        row = await self._shared()._one(
            "insert into project_media (project_id, kind, mime, filename, size_bytes, data) values (%s, %s, %s, %s, %s, %s) "
            "returning id, project_id, kind, mime, filename, size_bytes, created_at", (str(project_id), kind, mime, filename, len(data), data))
        assert row is not None
        return row

    async def list_project_media(self, project_id: UUID | str) -> list[dict]:
        return await self._fetch("select id, kind, mime, filename, size_bytes, created_at from project_media where project_id = %s order by id desc", (str(project_id),))

    async def get_project_media(self, media_id: int) -> dict | None:
        return await self._one("select id, project_id, kind, mime, filename, data from project_media where id = %s", (media_id,))

    async def latest_project_media(self, project_id: UUID | str) -> dict | None:
        return await self._one("select id, project_id, kind, mime, filename, data from project_media where project_id = %s order by id desc limit 1", (str(project_id),))

    async def delete_project_media(self, media_id: int) -> bool:
        rows = await self._shared()._fetch("delete from project_media where id = %s returning id", (media_id,))
        return bool(rows)

    async def set_project_image(self, project_id: UUID | str, url: str | None) -> dict | None:
        return await self._shared()._one("update projects set image_url = %s where id = %s returning *", (url, str(project_id)))

    # ---- unit layouts: the rooms of a unit, from which its 3D model is built ----

    async def get_layout(self, property_id: UUID | str) -> dict | None:
        row = await self._one("select layout from unit_layouts where property_id = %s", (str(property_id),))
        return row["layout"] if row else None

    async def set_layout(self, property_id: UUID | str, layout: dict) -> None:
        from psycopg.types.json import Jsonb

        await self._fetch(
            "insert into unit_layouts (property_id, layout) values (%s, %s) "
            "on conflict (org_id, property_id) do update set layout = excluded.layout, updated_at = now() returning property_id",
            (str(property_id), Jsonb(layout)))

    async def delete_layout(self, property_id: UUID | str) -> bool:
        return bool(await self._fetch("delete from unit_layouts where property_id = %s returning property_id", (str(property_id),)))

    async def units_with_layout(self) -> set[str]:
        return {str(r["property_id"]) for r in await self._fetch("select property_id from unit_layouts")}

    async def answered_price_requests(self, project_id: UUID | str) -> list[dict]:
        """Buyers who asked for this project's price and were told: the ones to tell when the price changes."""
        return await self._fetch(
            "select q.id, q.buyer_id from price_requests q where q.project_id = %s and q.status = 'ANSWERED' order by q.id", (str(project_id),))

    # ---- organizations, members and invitations ----
    # The first group reads and writes the tenancy tables for a given organization or person, which a tenant's own role may not
    # do; call them on `repo.as_platform()` after the caller's rights have been checked (app/access.py, app/org_api.py).

    async def organizations_by_id(self, org_ids: Sequence[UUID | str]) -> list[dict]:
        return await self._fetch("select id, slug, name, status from organizations where id = any(%s::uuid[])", ([str(o) for o in org_ids],))

    async def organization(self, org_id: UUID | str) -> dict | None:
        return await self._one("select id, slug, name, plan, status from organizations where id = %s", (str(org_id),))

    async def memberships_for_user(self, user_id: UUID | str) -> list[dict]:
        return await self._fetch(
            "select m.org_id, m.role, o.slug, o.name, o.plan, o.status from memberships m join organizations o on o.id = m.org_id "
            "where m.user_id = %s order by m.created_at, o.slug", (str(user_id),))

    async def create_organization(self, name: str, slug: str, owner_id: UUID | str) -> dict:
        """A new organization, its settings and its first owner, together or not at all. A taken slug raises UniqueViolation."""
        async with self._conn() as conn:
            async with conn.transaction():
                org = await (await conn.execute(
                    "insert into organizations (slug, name) values (%s, %s) returning id, slug, name, plan, status", (slug, name))).fetchone()
                await conn.execute("insert into org_settings (org_id, display_name) values (%s, %s)", (org["id"], name))
                await conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (org["id"], str(owner_id)))
                return org

    async def count_owned_organizations(self, user_id: UUID | str) -> int:
        rows = await self._fetch("select count(*)::int as n from memberships where user_id = %s and role = 'owner'", (str(user_id),))
        return rows[0]["n"]

    async def list_members(self, org_id: UUID | str) -> list[dict]:
        return await self._fetch(
            "select user_id, role, created_at from memberships where org_id = %s order by created_at, user_id", (str(org_id),))

    async def get_member(self, org_id: UUID | str, user_id: UUID | str) -> dict | None:
        return await self._one("select user_id, role from memberships where org_id = %s and user_id = %s", (str(org_id), str(user_id)))

    async def set_member_role(self, org_id: UUID | str, user_id: UUID | str, role: str) -> dict | None:
        """Raises RaiseException when this would leave the organization without an owner."""
        rows = await self._guarded(
            "update memberships set role = %s where org_id = %s and user_id = %s returning user_id, role", (role, str(org_id), str(user_id)))
        return rows[0] if rows else None

    async def remove_member(self, org_id: UUID | str, user_id: UUID | str) -> bool:
        """Raises RaiseException when this would leave the organization without an owner."""
        return bool(await self._guarded(
            "delete from memberships where org_id = %s and user_id = %s returning user_id", (str(org_id), str(user_id))))

    # Invitations belong to one organization like any private table, so these run as the caller's organization.

    async def create_invitation(self, email: str, role: str, token_hash: str, invited_by: UUID | str | None) -> dict:
        rows = await self._guarded(
            "insert into invitations (org_id, email, role, token_hash, invited_by) values (current_org(), %s, %s, %s, %s) "
            "returning id, email, role, expires_at", (email, role, token_hash, str(invited_by) if invited_by else None))
        return rows[0]

    async def usage_today(self) -> dict:
        """What this agency has used since midnight UTC: buyer messages and model calls, with its plan."""
        rows = await self._fetch(
            "with day as (select date_trunc('day', now() at time zone 'utc') at time zone 'utc' as since) "
            "select (select plan from organizations where id = current_org()) as plan, "
            "(select count(*)::int from chat_messages, day where role = 'buyer' and created_at >= day.since) as buyer_messages, "
            "(select count(*)::int from model_calls, day where created_at >= day.since) as model_calls")
        return rows[0]

    async def add_audit(self, org_id: UUID | str, actor_kind: str, actor_id: UUID | str | None, action: str, target: str | None, detail: dict) -> None:
        """Platform use: tenants may read their audit trail but not write it (migration 026)."""
        from psycopg.types.json import Jsonb

        await self.as_platform()._fetch(
            "insert into audit_log (org_id, actor_kind, actor_id, action, target, detail) values (%s, %s, %s, %s, %s, %s) returning id",
            (str(org_id), actor_kind, None if actor_id is None else str(actor_id), action, target, Jsonb(detail, dumps=lambda o: __import__("json").dumps(o, default=str))))

    async def list_audit(self, limit: int = 100, before_id: int | None = None) -> list[dict]:
        return await self._fetch(
            "select id, actor_kind, actor_id, action, target, detail, created_at from audit_log "
            "where (%s::bigint is null or id < %s) order by id desc limit %s", (before_id, before_id, limit))

    async def open_invitations(self) -> list[dict]:
        return await self._fetch(
            "select id, email, role, expires_at, created_at from invitations "
            "where accepted_at is null and expires_at > now() order by created_at")

    async def revoke_invitation(self, invitation_id: UUID | str) -> bool:
        return bool(await self._fetch(
            "delete from invitations where id = %s and accepted_at is null returning id", (str(invitation_id),)))

    async def accept_invitation(self, token_hash: str, user_id: UUID | str, email: str | None) -> dict | None:
        """Turn an open invitation into a membership for the person holding the link, if it was sent to their address.

        Returns the new membership, or None when the link is unknown, used or expired. Raises PermissionError when it was
        written for another address. Platform use: the person is not a member yet, so no organization is in scope.
        """
        async with self._conn() as conn:
            inv = await (await conn.execute(
                "select id, org_id, email, role from invitations where token_hash = %s and accepted_at is null and expires_at > now() "
                "for update", (token_hash,))).fetchone()
            if inv is None:
                return None
            if not email or email.strip().lower() != inv["email"].strip().lower():
                raise PermissionError("this invitation was sent to a different address")
            await conn.execute(
                "insert into memberships (org_id, user_id, role) values (%s, %s, %s) "
                "on conflict (org_id, user_id) do nothing", (inv["org_id"], str(user_id), inv["role"]))
            await conn.execute("update invitations set accepted_at = now() where id = %s", (inv["id"],))
            return await (await conn.execute(
                "select m.org_id, m.role, o.slug, o.name from memberships m join organizations o on o.id = m.org_id "
                "where m.org_id = %s and m.user_id = %s", (inv["org_id"], str(user_id)))).fetchone()

    # ---- channel settings and credentials (platform use; see app/runtime.py) ----

    async def org_profile(self, org_id: UUID | str) -> dict | None:
        return await self._one(
            "select o.id, o.slug, o.name, o.status, s.display_name, s.broker_name, s.broker_chat_id, s.timezone, s.persona, "
            "s.google_calendar_id, s.telegram_bot_username, s.telegram_connected_at "
            "from organizations o left join org_settings s on s.org_id = o.id where o.id = %s", (str(org_id),))

    async def org_by_slug(self, slug: str) -> dict | None:
        return await self._one("select id, slug, name, status from organizations where slug = %s", (slug,))

    async def active_org_ids(self) -> list[UUID]:
        rows = await self._fetch("select id from organizations where status = 'active' order by created_at, slug")
        return [r["id"] for r in rows]

    async def org_key(self, org_id: UUID | str) -> str | None:
        row = await self._one("select wrapped_key from org_keys where org_id = %s", (str(org_id),))
        return row["wrapped_key"] if row else None

    async def ensure_org_key(self, org_id: UUID | str, wrapped_key: str) -> str:
        """Keep the organization's data key if it has none yet, and return the one that is stored (a racing request's wins)."""
        rows = await self._fetch(
            "insert into org_keys (org_id, wrapped_key) values (%s, %s) on conflict (org_id) do update set wrapped_key = org_keys.wrapped_key "
            "returning wrapped_key", (str(org_id), wrapped_key))
        return rows[0]["wrapped_key"]

    async def org_secret_rows(self, org_id: UUID | str) -> list[dict]:
        return await self._fetch("select name, ciphertext from org_secrets where org_id = %s", (str(org_id),))

    async def put_org_secret(self, org_id: UUID | str, name: str, ciphertext: str) -> None:
        await self._fetch(
            "insert into org_secrets (org_id, name, ciphertext) values (%s, %s, %s) "
            "on conflict (org_id, name) do update set ciphertext = excluded.ciphertext, updated_at = now() returning name",
            (str(org_id), name, ciphertext))

    async def delete_org_secret(self, org_id: UUID | str, name: str) -> bool:
        return bool(await self._fetch("delete from org_secrets where org_id = %s and name = %s returning name", (str(org_id), name)))

    async def update_org_settings(self, org_id: UUID | str, **fields: Any) -> dict:
        """Change an organization's non-secret settings. Only the listed columns can be named."""
        allowed = {"display_name", "broker_name", "persona", "broker_chat_id", "timezone", "google_calendar_id",
                   "telegram_bot_username", "telegram_connected_at", "hero_property_id", "brand_color", "logo_url"}
        unknown = set(fields) - allowed
        if unknown or not fields:
            raise ValueError(f"cannot update settings: {sorted(unknown) or 'none given'}")
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
        rows = await self._fetch(
            sql.SQL("update org_settings set {}, updated_at = now() where org_id = %s returning org_id").format(sets),
            [*fields.values(), str(org_id)])
        if not rows:
            raise LookupError(f"organization {org_id} has no settings row")
        return (await self.org_profile(org_id)) or {}
