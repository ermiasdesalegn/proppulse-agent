"""Google Calendar through a service account, using only httpx and `cryptography` (no Google client libraries).

Setup, once: create a service account, download its JSON key, and share the broker's calendar with the service
account's email address ("Make changes to events"). Put the JSON (or its file path) in GOOGLE_SERVICE_ACCOUNT_JSON
and the calendar's id in GOOGLE_CALENDAR_ID.
"""
from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.calendar import BusinessHours, CalendarError, Slot, SlotUnavailable

SCOPE = "https://www.googleapis.com/auth/calendar"
API = "https://www.googleapis.com/calendar/v3"


def load_service_account(value: str) -> dict:
    """Accept the key as raw JSON or as a path to the JSON file, and check it has what signing needs."""
    value = value.strip()
    if not value.startswith("{") and '"client_email"' in value:
        value = "{" + value.strip().strip(",") + "}"  # pasted into a hosting dashboard without its outer braces
    try:
        # strict=False: a private key pasted with real line breaks instead of 
 is still read
        info = json.loads(value, strict=False) if value.startswith("{") else json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalendarError(f"could not read the service account key: {type(exc).__name__}") from None
    for field in ("client_email", "private_key"):
        if not info.get(field):
            raise CalendarError(f"the service account key has no {field}")
    info.setdefault("token_uri", "https://oauth2.googleapis.com/token")
    return info


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _parse(moment: str) -> datetime:
    return datetime.fromisoformat(moment.replace("Z", "+00:00"))


class GoogleCalendarService:
    def __init__(
        self,
        credentials: dict,
        calendar_id: str,
        hours: BusinessHours,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._creds, self._calendar_id, self.hours = credentials, calendar_id, hours
        self._key = serialization.load_pem_private_key(credentials["private_key"].encode(), password=None)
        self._transport, self._clock = transport, clock
        self._token: str | None = None
        self._token_expires = 0.0

    # ---------------- authentication ----------------

    def _assertion(self, now: float) -> str:
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(json.dumps({
            "iss": self._creds["client_email"], "scope": SCOPE, "aud": self._creds["token_uri"],
            "iat": int(now), "exp": int(now) + 3600,
        }).encode())
        signature = self._key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_b64(signature)}"

    async def _access_token(self, *, force: bool = False) -> str:
        now = self._clock()
        if not force and self._token and self._token_expires - 60 > now:
            return self._token
        async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
            res = await client.post(self._creds["token_uri"], data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": self._assertion(now)})
        if res.status_code != 200:
            raise CalendarError(f"Google rejected the service account sign-in ({res.status_code})")
        body = res.json()
        self._token, self._token_expires = body["access_token"], now + float(body.get("expires_in", 3600))
        return self._token

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            token = await self._access_token(force=attempt == 2)
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
                    res = await client.request(method, f"{API}{path}", json=body, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as exc:
                raise CalendarError(f"could not reach Google Calendar: {type(exc).__name__}") from None
            if res.status_code == 401 and attempt == 1:
                continue  # the cached token was refused: sign in again once
            if res.status_code >= 300:
                detail = ""
                try:
                    detail = res.json().get("error", {}).get("message", "")
                except ValueError:
                    pass
                raise CalendarError(f"Google Calendar returned {res.status_code}: {detail[:200]}")
            return res.json() if res.content else {}  # DELETE answers 204 with no body
        raise CalendarError("Google Calendar kept refusing the access token")

    # ---------------- calendar ----------------

    async def _busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": self._calendar_id}]}
        calendar = (await self._call("POST", "/freeBusy", body)).get("calendars", {}).get(self._calendar_id, {})
        if calendar.get("errors"):
            raise CalendarError(f"the calendar cannot be read: {calendar['errors'][0].get('reason', 'unknown')}")
        return [(_parse(b["start"]), _parse(b["end"])) for b in calendar.get("busy", [])]

    async def is_available(self, start: datetime, end: datetime) -> bool:
        if not self.hours.contains(start, end):
            return False  # no need to ask Google about a time the broker never takes visits
        window = (start - self.hours.buffer, end + self.hours.buffer)
        return not self.hours.conflicts(await self._busy(*window), start, end)

    async def find_slots(self, *, after: datetime, days: int, duration: timedelta, limit: int = 5) -> list[Slot]:
        busy = await self._busy(after - self.hours.buffer, after + timedelta(days=days + 1))
        return self.hours.free_slots(busy=busy, after=after, days=days, duration=duration, limit=limit)

    async def book(self, *, start: datetime, end: datetime, summary: str, description: str = "", location: str = "") -> str:
        if not await self.is_available(start, end):  # checked again at the last moment, in case someone else just booked it
            raise SlotUnavailable(f"{start.isoformat()} is not available")
        zone = str(self.hours.tz)
        event = await self._call("POST", f"/calendars/{quote(self._calendar_id, safe='')}/events", {
            "summary": summary, "description": description, "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": zone},
            "end": {"dateTime": end.isoformat(), "timeZone": zone},
        })
        return event["id"]

    async def cancel(self, event_id: str) -> None:
        try:
            await self._call("DELETE", f"/calendars/{quote(self._calendar_id, safe='')}/events/{quote(event_id, safe='')}")
        except CalendarError as exc:
            if "404" in str(exc) or "410" in str(exc):
                return  # already gone
            raise
