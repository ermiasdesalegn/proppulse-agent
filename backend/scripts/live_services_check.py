"""Live check of Google Calendar and Mapbox with the credentials in .env. Creates one test event and deletes it again.

    .venv\\Scripts\\python -m scripts.live_services_check
"""
import asyncio
import sys
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import make_pool
from app.repository import Repository
from app.services import build_services
from app.services.google_calendar import GoogleCalendarService
from app.services.routing import MapboxRoutingService, Stop, decode_polyline


async def check_calendar(s) -> bool:
    services = build_services(s)
    cal = services.calendar
    print(f"calendar implementation: {type(cal).__name__}")
    if not isinstance(cal, GoogleCalendarService):
        print("  NOT LIVE: credentials were not picked up")
        return False
    now = datetime.now(ZoneInfo(s.timezone))
    slots = await cal.find_slots(after=now, days=7, duration=timedelta(minutes=60), limit=3)
    print(f"  free slots read from the real calendar: {len(slots)}")
    if not slots:
        return False
    start, end = slots[0].start, slots[0].end
    print(f"  booking a test event at {start.isoformat()}")
    event_id = await cal.book(start=start, end=end, summary="PropPulse live check (test, safe to delete)", description="Created by scripts/live_services_check.py")
    try:
        busy_now = not await cal.is_available(start, end)
        print(f"  the slot now reads as busy: {busy_now}")
        again = await cal.find_slots(after=now, days=7, duration=timedelta(minutes=60), limit=3)
        gone = all(x.start != start for x in again)
        print(f"  the booked slot no longer offered: {gone}")
        return busy_now and gone
    finally:
        await cal._call("DELETE", f"/calendars/{quote(cal._calendar_id, safe='')}/events/{event_id}")
        print("  test event deleted again")


async def check_mapbox(s, repo) -> bool:
    rows = await repo.list_properties_for_map()
    seen, stops = set(), []
    for r in rows:
        key = (round(float(r["lat"]), 3), round(float(r["lng"]), 3))
        if r.get("lat") is not None and key not in seen:
            seen.add(key)
            stops.append(Stop(str(r["id"]), float(r["lat"]), float(r["lng"])))
        if len(stops) == 3:
            break
    origin = (s.office_lat, s.office_lng) if s.office_lat is not None and s.office_lng is not None else None
    print(f"mapbox: {len(stops)} stops, office origin: {origin}")
    plan = await MapboxRoutingService(s.mapbox_token).plan_route(stops, origin=origin)  # no fallback: a failure must show
    points = decode_polyline(plan.geometry)
    print(f"  order: {[o[:8] for o in plan.order]}")
    print(f"  {plan.distance_meters / 1000:.1f} km, {plan.duration_seconds / 60:.0f} min, {len(points)} polyline points")
    return len(plan.order) == len(stops) and plan.distance_meters > 0 and len(points) > 10


async def main() -> int:
    s = get_settings()
    pool = make_pool(s)
    await pool.open(wait=True, timeout=20)
    ok = True
    try:
        for name, run in (("calendar", lambda: check_calendar(s)), ("mapbox", lambda: check_mapbox(s, Repository(pool)))):
            try:
                result = await run()
            except Exception as exc:  # the message is already free of secrets
                result = False
                print(f"  {name} FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            print(f"{name}: {'ok' if result else 'FAILED'}\n")
            ok &= result
    finally:
        await pool.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
