import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import Settings
from app.services import build_services
from app.services.calendar import MockCalendarService, SlotUnavailable, parse_working_days
from app.services.email import EmailError, MockEmailService, ResendEmailService
from app.services.routing import MockRoutingService, Stop, encode_polyline
from app.services.telegram import BotApiTelegramService, MockTelegramService, TelegramError

TZ = ZoneInfo("Africa/Addis_Ababa")
HOUR = timedelta(hours=1)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)  # 25 Oct 2026 is a Sunday


def decode_polyline(s: str, precision: int = 5) -> list[tuple[float, float]]:
    pts, i, lat, lng = [], 0, 0, 0
    while i < len(s):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        pts.append((lat / 10**precision, lng / 10**precision))
    return pts


# ---------------- calendar ----------------

def test_working_days_parser():
    assert parse_working_days("Mon,Tue, Sat") == {0, 1, 5}


async def test_slots_skip_sunday_and_start_at_opening_hour():
    cal = MockCalendarService(working_days=parse_working_days("Mon,Tue,Wed,Thu,Fri,Sat"))
    slots = await cal.find_slots(after=at(25, 10), days=3, duration=HOUR, limit=3)
    assert slots[0].start == at(26, 9)
    assert all(s.start.weekday() != 6 for s in slots)


async def test_slots_stay_inside_business_hours_and_after_the_requested_time():
    cal = MockCalendarService()
    slots = await cal.find_slots(after=at(26, 16, 20), days=0, duration=HOUR, limit=10)
    assert [s.start.hour for s in slots] == [17]  # 16:00 is before 16:20; 18:00 would end after closing
    assert slots[0].end == at(26, 18)


async def test_booking_blocks_the_slot_and_its_travel_buffer():
    cal = MockCalendarService(buffer=timedelta(minutes=30))
    event_id = await cal.book(start=at(26, 9), end=at(26, 10), summary="Tour")
    assert event_id.startswith("mock-evt-")
    slots = await cal.find_slots(after=at(26, 8), days=0, duration=HOUR, limit=1)
    assert slots[0].start == at(26, 11)  # 10:00 is inside the 30 min buffer
    with pytest.raises(SlotUnavailable):
        await cal.book(start=at(26, 10), end=at(26, 11), summary="Clash")


# ---------------- routing ----------------

KAZANCHIS = Stop("kaz", 9.0147, 38.7636)
BOLE = Stop("bole", 8.9950, 38.7860)
AYAT = Stop("ayat", 9.0235, 38.8690)


async def test_route_visits_nearest_stop_first_and_returns_the_agreed_json_shape():
    plan = await MockRoutingService().plan_route([AYAT, BOLE, KAZANCHIS], origin=(9.0000, 38.7500))
    assert plan.order == ["kaz", "bole", "ayat"]
    assert set(plan.to_json()) == {"geometry", "distance_meters", "duration_seconds", "bbox"}
    assert plan.distance_meters > 10_000 and plan.duration_seconds > 0


async def test_route_geometry_decodes_back_to_the_stops_and_bbox_is_lng_lat_order():
    plan = await MockRoutingService().plan_route([AYAT, BOLE, KAZANCHIS], origin=(9.0000, 38.7500))
    pts = decode_polyline(plan.geometry)
    assert len(pts) == 4 and pts[0] == pytest.approx((9.0, 38.75), abs=1e-5)
    assert pts[-1] == pytest.approx((AYAT.lat, AYAT.lng), abs=1e-5)
    min_lng, min_lat, max_lng, max_lat = plan.bbox
    assert (min_lng, max_lng) == (38.75, 38.869) and (min_lat, max_lat) == (8.995, 9.0235)


async def test_single_stop_route_and_empty_input():
    plan = await MockRoutingService().plan_route([BOLE])
    assert plan.order == ["bole"] and plan.distance_meters == 0
    with pytest.raises(ValueError):
        await MockRoutingService().plan_route([])


def test_polyline_known_vector():
    # Reference example from Google's polyline algorithm documentation.
    assert encode_polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


# ---------------- email ----------------

def resend_with(handler, **kw):
    return ResendEmailService("re_test", "PropPulse <a@b.test>", transport=httpx.MockTransport(handler), **kw)


async def test_resend_sends_expected_request_and_returns_message_id():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(url=str(req.url), auth=req.headers["authorization"], idem=req.headers.get("idempotency-key"), body=json.loads(req.content))
        return httpx.Response(200, json={"id": "msg_123"})

    msg_id = await resend_with(handler).send(to="dev@x.test", subject="Hi", html="<p>x</p>", idempotency_key="k1")
    assert msg_id == "msg_123"
    assert seen["url"] == "https://api.resend.com/emails" and seen["auth"] == "Bearer re_test" and seen["idem"] == "k1"
    assert seen["body"] == {"from": "PropPulse <a@b.test>", "to": ["dev@x.test"], "subject": "Hi", "html": "<p>x</p>"}


async def test_demo_override_redirects_mail_and_keeps_the_original_recipient_visible():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "m"})

    await resend_with(handler, override_to="me@mine.test").send(to="sales@ayat.example", subject="Lead", html="x")
    assert seen["to"] == ["me@mine.test"] and "sales@ayat.example" in seen["subject"]


async def test_resend_failure_raises_email_error():
    with pytest.raises(EmailError, match="422"):
        await resend_with(lambda r: httpx.Response(422, json={"message": "bad"})).send(to="a@b.test", subject="s", html="h")


async def test_mock_email_records_messages():
    svc = MockEmailService()
    assert await svc.send(to="a@b.test", subject="s", html="h") == "mock-email-1"
    assert svc.sent[0]["to"] == "a@b.test"


# ---------------- telegram ----------------

TOKEN = "123456:SECRET-TOKEN"


def tg_with(handler):
    return BotApiTelegramService(TOKEN, transport=httpx.MockTransport(handler))


async def test_telegram_send_message_and_chat_action():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7} if "sendMessage" in req.url.path else True})

    tg = tg_with(handler)
    assert await tg.send_message(42, "hello", reply_markup={"inline_keyboard": []}) == 7
    await tg.send_chat_action(42)
    assert calls[0][0] == f"/bot{TOKEN}/sendMessage" and calls[0][1]["reply_markup"] == {"inline_keyboard": []}
    assert calls[1][1] == {"chat_id": 42, "action": "typing"}


async def test_telegram_downloads_a_voice_file_in_two_steps():
    def handler(req: httpx.Request):
        if req.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f1.oga"}})
        assert req.url.path == f"/file/bot{TOKEN}/voice/f1.oga"
        return httpx.Response(200, content=b"OggS-bytes")

    assert await tg_with(handler).download_file("FILEID") == b"OggS-bytes"


async def test_telegram_error_never_leaks_the_bot_token():
    def boom(req):
        raise httpx.ConnectError("cannot connect", request=req)

    with pytest.raises(TelegramError) as exc:
        await tg_with(boom).send_message(1, "x")
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None and exc.value.__suppress_context__

    with pytest.raises(TelegramError, match="chat not found"):
        await tg_with(lambda r: httpx.Response(400, json={"ok": False, "description": "chat not found"})).send_message(1, "x")


async def test_mock_telegram_records_traffic():
    tg = MockTelegramService(files={"f": b"abc"})
    await tg.send_message(1, "hi")
    await tg.send_chat_action(1)
    assert tg.messages[0]["text"] == "hi" and tg.actions == [(1, "typing")] and await tg.download_file("f") == b"abc"


# ---------------- container ----------------

def test_container_uses_mocks_without_credentials_and_live_services_with_them():
    bare = build_services(Settings(_env_file=None, resend_api_key="", telegram_bot_token=""))
    assert isinstance(bare.email, MockEmailService) and isinstance(bare.telegram, MockTelegramService)
    assert isinstance(bare.calendar, MockCalendarService)

    live = build_services(Settings(_env_file=None, resend_api_key="re_x", telegram_bot_token="1:x", email_from="a@b.test"))
    assert isinstance(live.email, ResendEmailService) and isinstance(live.telegram, BotApiTelegramService)


def test_container_refuses_live_backends_without_credentials_and_unknown_names():
    from app.services.calendar import CalendarError

    with pytest.raises(CalendarError, match="missing"):
        build_services(Settings(_env_file=None, calendar_backend="google"))
    with pytest.raises(ValueError, match="MAPBOX_TOKEN"):
        build_services(Settings(_env_file=None, routing_backend="mapbox"))
    with pytest.raises(ValueError, match="unknown backend"):
        build_services(Settings(_env_file=None, routing_backend="osrm"))


def test_auto_picks_live_calendar_and_routing_only_when_credentials_exist():
    from app.services.google_calendar import GoogleCalendarService
    from app.services.routing import ResilientRoutingService
    from tests.test_google_calendar import SERVICE_ACCOUNT

    bare = build_services(Settings(_env_file=None))
    assert isinstance(bare.calendar, MockCalendarService) and isinstance(bare.routing, MockRoutingService)
    live = build_services(Settings(_env_file=None, google_service_account_json=json.dumps(SERVICE_ACCOUNT), google_calendar_id="cal@x.test", mapbox_token="pk.test"))
    assert isinstance(live.calendar, GoogleCalendarService) and isinstance(live.routing, ResilientRoutingService)
    forced_mock = build_services(Settings(_env_file=None, calendar_backend="mock", routing_backend="mock", mapbox_token="pk.test",
                                          google_service_account_json=json.dumps(SERVICE_ACCOUNT), google_calendar_id="cal@x.test"))
    assert isinstance(forced_mock.calendar, MockCalendarService) and isinstance(forced_mock.routing, MockRoutingService)
