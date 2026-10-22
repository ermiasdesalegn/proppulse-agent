"""All SQL used by the agent and API lives here. Rows come back as plain dicts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence
from uuid import UUID

from psycopg import sql
from pydantic import BaseModel

BUYER_FIELDS = {
    "full_name", "phone", "email", "max_budget_etb", "min_bedrooms",
    "preferred_sub_cities", "lead_score", "deal_stage",
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
    def __init__(self, pool: Any):
        self._pool = pool

    async def _fetch(self, query: str | sql.Composable, params: Sequence[Any] = ()) -> list[dict]:
        async with self._pool.connection() as conn:
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
        rows = await self._fetch(
            "select * from match_properties_hybrid(%s::vector, %s, %s, %s::text[], %s, %s)",
            (vector_literal(embedding), max_budget_etb, min_bedrooms, list(sub_cities or []), count, threshold),
        )
        return [PropertyMatch(**r) for r in rows]

    async def get_properties(self, ids: Sequence[UUID | str]) -> list[dict]:
        """Full property rows (no embedding) joined with developer contact details, in the order requested."""
        rows = await self._fetch(
            "select p.id, p.developer_id, p.title, p.project_name, p.unit_type, p.sub_city, p.lat, p.lng, p.price_etb, "
            "p.bedrooms, p.sqm, p.down_payment_pct, p.construction_stage_pct, p.est_rental_yield_pct, p.delivery_date, "
            "p.status, p.description, p.floor_plan_image_url, p.vision_amenities, "
            "d.name as developer_name, d.slug as developer_slug, d.sales_email, d.commission_rate_pct "
            "from properties p join developers d on d.id = p.developer_id where p.id = any(%s::uuid[])",
            ([str(i) for i in ids],),
        )
        by_id = {str(r["id"]): r for r in rows}
        return [by_id[str(i)] for i in ids if str(i) in by_id]

    async def get_developer_by_slug(self, slug: str) -> dict | None:
        return await self._one("select * from developers where slug = %s", (slug,))

    # ---- buyers ----

    async def get_or_create_buyer(self, thread_id: str, telegram_id: int | None = None) -> dict:
        row = await self._one(
            "insert into buyers_crm (thread_id, telegram_id) values (%s, %s) "
            "on conflict (thread_id) do update set thread_id = excluded.thread_id returning *",
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
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
        row = await self._one(
            sql.SQL("update buyers_crm set {} where id = %s returning *").format(sets),
            [*fields.values(), str(buyer_id)],
        )
        if row is None:
            raise LookupError(f"buyer {buyer_id} not found")
        return row

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
        self, buyer_id: UUID | str, role: str, content: str, *, source: str = "text", telegram_file_id: str | None = None
    ) -> dict:
        row = await self._one(
            "insert into chat_messages (buyer_id, role, content, source, telegram_file_id) values (%s, %s, %s, %s, %s) returning *",
            (str(buyer_id), role, content, source, telegram_file_id),
        )
        assert row is not None
        return row

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
    ) -> dict:
        """Create the tour and its ordered stops atomically. `stops` is already in visiting order."""
        from psycopg.types.json import Jsonb

        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "insert into tours_and_appointments (buyer_id, scheduled_start, calendar_event_id, mapbox_route_json) "
                    "values (%s, %s, %s, %s) returning *",
                    (str(buyer_id), scheduled_start, calendar_event_id, Jsonb(route_json) if route_json else None),
                )
                tour = await cur.fetchone()
                for order, property_id in enumerate(stops, start=1):
                    await conn.execute(
                        "insert into tour_properties (tour_id, property_id, stop_order) values (%s, %s, %s)",
                        (tour["id"], str(property_id), order),
                    )
        return tour

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
        async with self._pool.connection() as conn:
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
            "select l.*, b.full_name as buyer_name, d.name as developer_name, p.title as property_title "
            "from commission_ledger l join buyers_crm b on b.id = l.buyer_id "
            "join developers d on d.id = l.developer_id join properties p on p.id = l.property_id "
            "where (%s::text is null or l.status = %s) order by l.created_at desc",
            (status, status),
        )
