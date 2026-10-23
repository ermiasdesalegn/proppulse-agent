"""Reset the demo: wipe the conversations and everything they caused, restore the demo catalogue.

This is destructive on purpose, so the endpoint that calls it is switched off unless DEMO_RESET_ENABLED is set and the caller types RESET.
"""
from __future__ import annotations

import logging
from typing import Any

from app.demo_data import DEVELOPERS, P
from app.ingest.text import content_hash

log = logging.getLogger(__name__)

# Deleted in this order: ledger rows block deleting buyers, and tours (with their stops) go with the buyer.
CLEARED = ("proactive_events", "project_posts", "commission_ledger", "buyers_crm", "channel_posts", "ingest_runs", "inbound_announcements", "event_log", "model_calls", "price_history")


async def reset_demo(repo: Any, calendar: Any = None) -> dict:
    """Returns how many rows were removed from each table, and what was restored."""
    report: dict[str, Any] = {"deleted": {}, "calendar_events_cancelled": 0}

    if calendar is not None:  # events this system put on the real calendar are removed with the tours that made them
        for tour in await repo._fetch("select calendar_event_id from tours_and_appointments where calendar_event_id is not null"):
            try:
                await calendar.cancel(tour["calendar_event_id"])
                report["calendar_events_cancelled"] += 1
            except Exception:
                log.exception("could not cancel calendar event %s", tour["calendar_event_id"])

    for table in CLEARED:
        rows = await repo._fetch(f"with gone as (delete from {table} returning 1) select count(*)::int as n from gone")
        report["deleted"][table] = rows[0]["n"]

    # Units that came from scraping or announcements are not part of the demo; the seeded ones go back to their original price.
    extra = await repo._fetch("with gone as (delete from properties where coalesce(vision_amenities->>'source', '') <> 'seed' returning 1) select count(*)::int as n from gone")
    report["deleted"]["added_properties"] = extra[0]["n"]
    restored = 0
    for slug, project, unit_type, _city, _geo, price, _beds, sqm, down, stage, yld, delivery, *_ in P:
        rows = await repo._fetch(
            "update properties set price_etb = %s, status = 'AVAILABLE', down_payment_pct = %s, construction_stage_pct = %s, est_rental_yield_pct = %s, delivery_date = %s "
            "where content_hash = %s returning id", (price, down, stage, yld, delivery, content_hash(slug, project, unit_type, sqm)))
        restored += len(rows)
    report["restored_properties"] = restored

    from psycopg.types.json import Jsonb

    for d in DEVELOPERS:
        await repo._fetch("update developers set payment_plan = %s where slug = %s returning id", (Jsonb(d["payment_plan"]), d["slug"]))

    from app.demo_projects import restore_demo_projects

    report["projects"] = await restore_demo_projects(repo)  # price requests went with the buyers; prices and offers go back to the demo state

    # The agent's saved conversations (LangGraph checkpoints) belong to the buyers just removed.
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        exists = await repo._fetch("select to_regclass(%s) as t", (table,))
        if exists and exists[0]["t"]:
            rows = await repo._fetch(f"with gone as (delete from {table} where thread_id like 'tg:%%' or thread_id like 'web:%%' returning 1) select count(*)::int as n from gone")
            report["deleted"][table] = rows[0]["n"]
    return report
