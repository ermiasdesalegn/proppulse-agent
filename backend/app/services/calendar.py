from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_working_days(spec: str) -> set[int]:
    return {_DAYS[d.strip().lower()[:3]] for d in spec.split(",") if d.strip()}


class SlotUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


class CalendarService(Protocol):
    async def find_slots(self, *, after: datetime, days: int, duration: timedelta, limit: int = 5) -> list[Slot]: ...

    async def book(self, *, start: datetime, end: datetime, summary: str, description: str = "", location: str = "") -> str:
        """Return the calendar event id. Raises SlotUnavailable if the time is taken."""


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
        self._tz = ZoneInfo(tz)
        self._start_hour, self._end_hour = start_hour, end_hour
        self._working_days = working_days if working_days is not None else {0, 1, 2, 3, 4, 5}
        self._buffer = buffer
        self.events: dict[str, dict] = {}

    def _conflicts(self, start: datetime, end: datetime) -> bool:
        return any(
            start < ev["end"] + self._buffer and end + self._buffer > ev["start"] for ev in self.events.values()
        )

    async def find_slots(self, *, after: datetime, days: int, duration: timedelta, limit: int = 5) -> list[Slot]:
        after = after.astimezone(self._tz)
        found: list[Slot] = []
        for offset in range(days + 1):
            day = (after + timedelta(days=offset)).date()
            if day.weekday() not in self._working_days:
                continue
            for hour in range(self._start_hour, self._end_hour):
                start = datetime.combine(day, time(hour), tzinfo=self._tz)
                end = start + duration
                if start < after or end > datetime.combine(day, time(self._end_hour), tzinfo=self._tz):
                    continue
                if not self._conflicts(start, end):
                    found.append(Slot(start, end))
                    if len(found) >= limit:
                        return found
        return found

    async def book(self, *, start: datetime, end: datetime, summary: str, description: str = "", location: str = "") -> str:
        if self._conflicts(start, end):
            raise SlotUnavailable(f"{start.isoformat()} is not available")
        event_id = f"mock-evt-{uuid.uuid4().hex[:12]}"
        self.events[event_id] = dict(start=start, end=end, summary=summary, description=description, location=location)
        return event_id
