import base64
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.calendar import BusinessHours, CalendarError, SlotUnavailable
from app.services.google_calendar import GoogleCalendarService, load_service_account

TZ = ZoneInfo("Africa/Addis_Ababa")
HOUR = timedelta(hours=1)
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "proppulse@demo-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
    "private_key": _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
}
CAL = "broker@example.test"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)  # 2 Nov 2026 is a Monday


def b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class FakeGoogle:
    def __init__(self):
        self.calls, self.busy, self.inserted = [], [], []
        self.token_count, self.fail_next_api = 0, []
        self.freebusy_errors = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.calls.append((req.method, req.url.path))
        if url == SERVICE_ACCOUNT["token_uri"]:
            self.token_count += 1
            self.last_assertion = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            return httpx.Response(200, json={"access_token": f"token-{self.token_count}", "expires_in": 3600})
        self.last_auth = req.headers.get("authorization")
        if self.fail_next_api:
            return self.fail_next_api.pop(0)
        if req.url.path.endswith("/freeBusy"):
            body = json.loads(req.content)
            self.freebusy_request = body
            entry = {"busy": [{"start": s.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), "end": e.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")} for s, e in self.busy]}
            if self.freebusy_errors:
                entry["errors"] = self.freebusy_errors
            return httpx.Response(200, json={"calendars": {CAL: entry}})
        if req.url.path.endswith("/events"):
            body = json.loads(req.content)
            self.inserted.append((req.url.raw_path.decode(), body))
            return httpx.Response(200, json={"id": "evt_123"})
        return httpx.Response(404, json={"error": {"message": "unexpected call"}})


def make(fake=None, clock=None):
    fake = fake or FakeGoogle()
    now = clock or [1_000_000.0]
    hours = BusinessHours(TZ, 9, 18, frozenset({0, 1, 2, 3, 4, 5}), timedelta(minutes=30))
    service = GoogleCalendarService(SERVICE_ACCOUNT, CAL, hours, transport=httpx.MockTransport(fake), clock=lambda: now[0])
    return service, fake, now


# ---------------- sign-in ----------------

async def test_the_sign_in_assertion_is_a_valid_signed_jwt():
    service, fake, _ = make()
    await service.is_available(at(2, 10), at(2, 11))
    form = fake.last_assertion
    assert form["grant_type"] == "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
    header_b64, claims_b64, sig_b64 = form["assertion"].split(".")
    assert json.loads(b64d(header_b64)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(b64d(claims_b64))
    assert claims["iss"] == SERVICE_ACCOUNT["client_email"] and claims["scope"] == "https://www.googleapis.com/auth/calendar"
    assert claims["aud"] == SERVICE_ACCOUNT["token_uri"] and claims["exp"] - claims["iat"] == 3600
    _KEY.public_key().verify(b64d(sig_b64), f"{header_b64}.{claims_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())  # raises if wrong


async def test_the_access_token_is_reused_until_shortly_before_it_expires():
    service, fake, now = make()
    await service.is_available(at(2, 10), at(2, 11))
    await service.is_available(at(2, 12), at(2, 13))
    assert fake.token_count == 1 and fake.last_auth == "Bearer token-1"
    now[0] += 3600 - 30  # inside the 60 s safety margin
    await service.is_available(at(2, 12), at(2, 13))
    assert fake.token_count == 2 and fake.last_auth == "Bearer token-2"


async def test_a_refused_token_is_replaced_once_and_the_request_retried():
    service, fake, _ = make()
    fake.fail_next_api = [httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})]
    assert await service.is_available(at(2, 10), at(2, 11)) is True
    assert fake.token_count == 2 and fake.last_auth == "Bearer token-2"


# ---------------- availability and slots ----------------

async def test_a_free_time_inside_business_hours_is_available():
    service, fake, _ = make()
    assert await service.is_available(at(2, 10), at(2, 11)) is True
    sent = fake.freebusy_request
    assert sent["items"] == [{"id": CAL}] and "T09:30" in sent["timeMin"], "the buffer is included in the window asked about"


async def test_busy_times_and_the_travel_buffer_block_a_slot():
    service, fake, _ = make()
    fake.busy = [(at(2, 9), at(2, 10))]
    assert await service.is_available(at(2, 9, 30), at(2, 10, 30)) is False   # overlaps
    assert await service.is_available(at(2, 10), at(2, 11)) is False          # inside the 30 min buffer
    assert await service.is_available(at(2, 11), at(2, 12)) is True


async def test_times_outside_business_hours_are_refused_without_asking_google():
    service, fake, _ = make()
    assert await service.is_available(at(1, 10), at(1, 11)) is False   # Sunday
    assert await service.is_available(at(2, 17), at(2, 19)) is False   # runs past closing
    assert await service.is_available(at(2, 7), at(2, 8)) is False     # before opening
    assert fake.calls == []


async def test_find_slots_uses_one_lookup_and_skips_busy_time_and_sundays():
    service, fake, _ = make()
    fake.busy = [(at(2, 9), at(2, 10))]
    slots = await service.find_slots(after=at(1, 8), days=2, duration=HOUR, limit=3)
    assert [s.start for s in slots] == [at(2, 11), at(2, 12), at(2, 13)]
    assert [path for _, path in fake.calls].count("/calendar/v3/freeBusy") == 1


# ---------------- booking ----------------

async def test_booking_creates_the_event_with_the_right_times_and_returns_its_id():
    service, fake, _ = make()
    event_id = await service.book(start=at(3, 10), end=at(3, 11), summary="Site visit: Abel", description="Abel 0911", location="Bole")
    assert event_id == "evt_123"
    path, body = fake.inserted[0]
    assert path == "/calendar/v3/calendars/broker%40example.test/events", "the calendar id is URL-encoded"
    assert body["summary"] == "Site visit: Abel" and body["location"] == "Bole"
    assert body["start"] == {"dateTime": "2026-11-03T10:00:00+03:00", "timeZone": "Africa/Addis_Ababa"}
    assert body["end"]["dateTime"] == "2026-11-03T11:00:00+03:00"


async def test_booking_a_taken_time_raises_and_creates_nothing():
    service, fake, _ = make()
    fake.busy = [(at(3, 10), at(3, 11))]
    with pytest.raises(SlotUnavailable):
        await service.book(start=at(3, 10), end=at(3, 11), summary="x")
    assert fake.inserted == []


# ---------------- failures ----------------

async def test_api_errors_become_calendar_errors_without_leaking_credentials():
    service, fake, _ = make()
    fake.fail_next_api = [httpx.Response(403, json={"error": {"message": "Calendar usage limits exceeded"}})]
    with pytest.raises(CalendarError) as exc:
        await service.is_available(at(2, 10), at(2, 11))
    text = str(exc.value)
    assert "403" in text and "limits exceeded" in text
    assert "BEGIN" not in text and "token-1" not in text and SERVICE_ACCOUNT["client_email"] not in text


async def test_an_unshared_calendar_is_reported_clearly():
    service, fake, _ = make()
    fake.freebusy_errors = [{"domain": "global", "reason": "notFound"}]
    with pytest.raises(CalendarError, match="notFound"):
        await service.is_available(at(2, 10), at(2, 11))


async def test_network_failures_and_sign_in_rejection_are_calendar_errors():
    def down(req):
        raise httpx.ConnectError("no route", request=req)

    hours = BusinessHours(TZ)
    offline = GoogleCalendarService(SERVICE_ACCOUNT, CAL, hours, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600}) if "oauth2" in str(r.url) else down(r)))
    with pytest.raises(CalendarError, match="could not reach"):
        await offline.is_available(at(2, 10), at(2, 11))

    rejected = GoogleCalendarService(SERVICE_ACCOUNT, CAL, hours, transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"})))
    with pytest.raises(CalendarError, match="sign-in"):
        await rejected.is_available(at(2, 10), at(2, 11))


# ---------------- loading the key ----------------

def test_service_account_loads_from_json_text_or_a_file(tmp_path):
    raw = json.dumps({k: v for k, v in SERVICE_ACCOUNT.items() if k != "token_uri"})
    assert load_service_account(raw)["token_uri"] == "https://oauth2.googleapis.com/token"
    path = tmp_path / "key.json"
    path.write_text(raw, encoding="utf-8")
    assert load_service_account(str(path))["client_email"] == SERVICE_ACCOUNT["client_email"]


@pytest.mark.parametrize("bad", ["", "not json", "{}", '{"client_email": "a@b.c"}', "C:/definitely/not/a/file.json"])
def test_a_broken_key_is_a_clear_error(bad):
    with pytest.raises(CalendarError):
        load_service_account(bad)


async def test_a_response_with_no_body_is_accepted():
    service, fake, _ = make()
    fake.fail_next_api = [httpx.Response(204)]  # what Google answers to a DELETE
    assert await service._call("DELETE", "/calendars/x/events/evt_123") == {}
