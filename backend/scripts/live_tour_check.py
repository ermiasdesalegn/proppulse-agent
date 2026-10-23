"""Book a three-developer tour through the real scheduler with the REAL Google Calendar and Mapbox.

Email and Telegram are mocks, and every database change is rolled back. The one real side effect, the calendar event, is deleted
at the end.

    .venv\\Scripts\\python -m scripts.live_tour_check
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import quote

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.agent.deps import AgentDeps
from app.agent.nodes import AgentNodes
from app.config import get_settings
from app.repository import Repository
from app.services import Services, build_services
from app.services.email import MockEmailService
from app.services.google_calendar import GoogleCalendarService
from app.services.routing import ResilientRoutingService
from app.services.telegram import MockTelegramService


class OneConnection:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


async def main() -> int:
    s = get_settings().model_copy(update={"broker_telegram_chat_id": "broker-chat", "demo_fallback_email": ""})
    live = build_services(s)
    if not isinstance(live.calendar, GoogleCalendarService) or not isinstance(live.routing, ResilientRoutingService):
        print("Calendar or Mapbox credentials are not configured: this check needs both.")
        return 1
    email, telegram = MockEmailService(), MockTelegramService()
    services = Services(calendar=live.calendar, routing=live.routing, email=email, telegram=telegram)

    conn = await AsyncConnection.connect(s.database_url, row_factory=dict_row, autocommit=False, prepare_threshold=None)
    event_id = None
    ok = False
    try:
        repo = Repository(OneConnection(conn))
        rows = await repo._fetch(
            "select distinct on (developer_id) id from properties where status = 'AVAILABLE' and lat is not null order by developer_id, price_etb")
        ids = [str(r["id"]) for r in rows][:3]
        props = await repo.get_properties(ids)
        print(f"properties: {', '.join(p['title'] + ' (' + p['developer_name'] + ')' for p in props)}")
        if len({p["developer_slug"] for p in props}) < 3:
            print("need three developers with available units")
            return 1

        buyer = await repo.get_or_create_buyer("live-check:three-developer-tour")
        nodes = AgentNodes(AgentDeps(gateway=None, repo=repo, services=services, settings=s))
        now = nodes.d.now()
        first = (await live.calendar.find_slots(after=now + timedelta(days=1), days=10, duration=timedelta(hours=4), limit=1))[0]
        print(f"first free 4-hour window: {first.start.isoformat()}")

        out = await nodes.scheduler({
            "buyer_id": str(buyer["id"]), "intent": "book_tour", "selected_property_ids": ids, "chat_id": 4242,
            "buyer_profile": {"full_name": "Live Check", "phone": "+251900000000", "email": "buyer@live-check.test"},
            "tour_start_iso": first.start.isoformat(), "active_matches": [],
        })
        print("\nreply:\n" + out["reply"] + "\n")
        tour = (await repo._fetch("select * from tours_and_appointments where buyer_id = %s", (str(buyer["id"]),)))[0]
        event_id = tour["calendar_event_id"]
        stops = await repo.get_tour_stops(tour["id"])
        route = tour["mapbox_route_json"] or {}

        checks = {
            "booked": bool(out.get("booked")),
            "three stops": len(stops) == 3,
            "route from the real Mapbox": bool(route.get("distance_meters")) and route.get("geometry") not in (None, ""),
            "the event exists on the real calendar": event_id is not None and not await live.calendar.is_available(first.start, first.start + timedelta(hours=1)),
            "one registration email per developer": sorted(m["to"] for m in email.sent if "sales@" in m["to"]) == sorted(p["sales_email"] for p in props),
            "itinerary email with the PDF": any(m["to"] == "buyer@live-check.test" and any(a.content.startswith(b"%PDF") for a in m["attachments"]) for m in email.sent),
            "brochure document on Telegram": len(telegram.documents) == 1,
            "dossier to the broker": any("PRE-TOUR DOSSIER" in m["text"] for m in telegram.messages if m["chat_id"] == "broker-chat"),
        }
        for name, passed in checks.items():
            print(f"  {'ok  ' if passed else 'FAIL'} {name}")
        print(f"\nroute: {route.get('distance_meters', 0) / 1000:.1f} km, {route.get('duration_seconds', 0) // 60} min")
        ok = all(checks.values())
    finally:
        await conn.rollback()
        await conn.close()
        if event_id:
            await live.calendar._call("DELETE", f"/calendars/{quote(live.calendar._calendar_id, safe='')}/events/{event_id}")
            print("test calendar event deleted; database changes rolled back")
    print("RESULT:", "ok" if ok else "CHECK THE OUTPUT ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
