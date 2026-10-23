from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

Busy = Sequence[tuple[datetime, datetime]]


def parse_working_days(spec: str) -> set[int]:
    return {_DAYS[d.strip().lower()[:3]] for d in spec.split(",") if d.strip()}


class SlotUnavailable(Exception):
    pass


class CalendarError(Exception):
    """The calendar could not be read or written (network, credentials, quota)."""


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BusinessHours:
    """When the broker takes visits, and how much travel time to leave between them. Every calendar backend uses this."""

    tz: ZoneInfo
    start_hour: int = 9
    end_hour: int = 18
    working_days: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5})
    buffer: timedelta = timedelta(minutes=30)

    def contains(self, start: datetime, end: datetime) -> bool:
        start, end = start.astimezone(self.tz), end.astimezone(self.tz)
        opens = datetime.combine(start.date(), time(self.start_hour), tzinfo=self.tz)
        closes = datetime.combine(start.date(), time(self.end_hour), tzinfo=self.tz)
        return start.weekday() in self.working_days and start >= opens and end <= closes

    def conflicts(self, busy: Busy, start: datetime, end: datetime) -> bool:
        return any(start < b_end + self.buffer and end + self.buffer > b_start for b_start, b_end in busy)

    def is_free(self, busy: Busy, start: datetime, end: datetime) -> bool:
        return self.contains(start, end) and not self.conflicts(busy, start, end)

    def free_slots(self, *, busy: Busy, after: datetime, days: int, duration: timedelta, limit: int) -> list[Slot]:
        after = after.astimezone(self.tz)
        found: list[Slot] = []
        for offset in range(days + 1):
            day = (after + timedelta(days=offset)).date()
            if day.weekday() not in self.working_days:
                continue
            for hour in range(self.start_hour, self.end_hour):
                start = datetime.combine(day, time(hour), tzinfo=self.tz)
                end = start + duration
                if start < after or end > datetime.combine(day, time(self.end_hour), tzinfo=self.tz):
                    continue
                if not self.conflicts(busy, start, end):
                    found.append(Slot(start, end))
                    if len(found) >= limit:
                        return found
        return found


class CalendarService(Protocol):
    async def is_available(self, start: datetime, end: datetime) -> bool: ...

    async def find_slots(self, *, after: datetime, days: int, duration: timedelta, limit: int = 5) -> list[Slot]: ...

    async def book(self, *, start: datetime, end: datetime, summary: str, description: str = "", location: str = "") -> str:
        """Return the calendar event id. Raises SlotUnavailable if the time is taken."""

    async def cancel(self, event_id: str) -> None:
        """Remove an event this service created. Cancelling one that is already gone is not an error."""


class MockCalendarService:
    """In-memory calendar with business hours, working days and travel buffer."""

    def __init__(
        self,
        tz: str = "Africa/Addis_Ababa",
        start_hour: int = 9,
        end_hour: int = 18,
        working_days: set[int] | None = None,
        buffer: timedelta = timedelta(minutes=30),
    ):
        self.hours = BusinessHours(
            ZoneInfo(tz), start_hour, end_hour,
            frozenset(working_days) if working_days is not None else frozenset({0, 1, 2, 3, 4, 5}), buffer,
        )
        self.events: dict[str, dict] = {}

    def _busy(self) -> list[tuple[datetime, datetime]]:
        return [(ev["start"], ev["end"]) for ev in self.events.values()]

    async def is_available(self, start: datetime, end: datetime) -> bool:
        return self.hours.is_free(self._busy(), start, end)

    async def find_slots(self, *, after: datetime, days: int, duration: timedelta, limit: int = 5) -> list[Slot]:
        return self.hours.free_slots(busy=self._busy(), after=after, days=days, duration=duration, limit=limit)

    async def book(self, *, start: datetime, end: datetime, summary: str, description: str = "", location: str = "") -> str:
        if not await self.is_available(start, end):
            raise SlotUnavailable(f"{start.isoformat()} is not available")
        event_id = f"mock-evt-{uuid.uuid4().hex[:12]}"
        self.events[event_id] = dict(start=start, end=end, summary=summary, description=description, location=location)
        return event_id

    async def cancel(self, event_id: str) -> None:
        self.events.pop(event_id, None)
