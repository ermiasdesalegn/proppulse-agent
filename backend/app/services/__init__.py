from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.calendar import BusinessHours, CalendarError, CalendarService, MockCalendarService, parse_working_days
from app.services.email import EmailService, MockEmailService, ResendEmailService
from app.services.google_calendar import GoogleCalendarService, load_service_account
from app.services.routing import MapboxRoutingService, MockRoutingService, ResilientRoutingService, RoutingService
from app.services.telegram import BotApiTelegramService, MockTelegramService, TelegramService


@dataclass
class Services:
    calendar: CalendarService
    routing: RoutingService
    email: EmailService
    telegram: TelegramService


def _wants_live(backend: str, live_name: str, have_credentials: bool) -> bool:
    if backend == live_name:
        return True
    if backend in ("auto",):
        return have_credentials
    if backend == "mock":
        return False
    raise ValueError(f"unknown backend {backend!r}; use 'mock', '{live_name}' or 'auto'")


def build_services(s: Settings) -> Services:
    """Pick implementations from config so graph nodes never construct their own."""
    hours = BusinessHours(
        ZoneInfo(s.timezone), s.business_hours_start, s.business_hours_end,
        frozenset(parse_working_days(s.working_days)), timedelta(minutes=s.tour_buffer_minutes),
    )
    if _wants_live(s.calendar_backend, "google", bool(s.google_service_account_json and s.google_calendar_id)):
        if not (s.google_service_account_json and s.google_calendar_id):
            raise CalendarError("calendar_backend is 'google' but GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_CALENDAR_ID is missing")
        calendar: CalendarService = GoogleCalendarService(load_service_account(s.google_service_account_json), s.google_calendar_id, hours)
    else:
        calendar = MockCalendarService(
            tz=s.timezone, start_hour=s.business_hours_start, end_hour=s.business_hours_end,
            working_days=parse_working_days(s.working_days), buffer=timedelta(minutes=s.tour_buffer_minutes),
        )

    if _wants_live(s.routing_backend, "mapbox", bool(s.mapbox_token)):
        if not s.mapbox_token:
            raise ValueError("routing_backend is 'mapbox' but MAPBOX_TOKEN is missing")
        routing: RoutingService = ResilientRoutingService(MapboxRoutingService(s.mapbox_token), MockRoutingService())
    else:
        routing = MockRoutingService()

    live_email = s.email_backend == "resend" or (s.email_backend == "auto" and bool(s.resend_api_key))
    email = (
        ResendEmailService(s.resend_api_key, s.email_from, override_to=s.demo_fallback_email)
        if live_email
        else MockEmailService()
    )

    live_tg = s.telegram_backend == "live" or (s.telegram_backend == "auto" and bool(s.telegram_bot_token))
    telegram = BotApiTelegramService(s.telegram_bot_token) if live_tg else MockTelegramService()

    return Services(calendar=calendar, routing=routing, email=email, telegram=telegram)
