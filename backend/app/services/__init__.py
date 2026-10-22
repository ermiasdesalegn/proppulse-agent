from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.config import Settings
from app.services.calendar import CalendarService, MockCalendarService, parse_working_days
from app.services.email import EmailService, MockEmailService, ResendEmailService
from app.services.routing import MockRoutingService, RoutingService
from app.services.telegram import BotApiTelegramService, MockTelegramService, TelegramService


@dataclass
class Services:
    calendar: CalendarService
    routing: RoutingService
    email: EmailService
    telegram: TelegramService


def build_services(s: Settings) -> Services:
    """Pick implementations from config so graph nodes never construct their own."""
    if s.calendar_backend != "mock":
        raise NotImplementedError(f"calendar backend '{s.calendar_backend}' is not built yet")
    if s.routing_backend != "mock":
        raise NotImplementedError(f"routing backend '{s.routing_backend}' is not built yet")

    calendar = MockCalendarService(
        tz=s.timezone,
        start_hour=s.business_hours_start,
        end_hour=s.business_hours_end,
        working_days=parse_working_days(s.working_days),
        buffer=timedelta(minutes=s.tour_buffer_minutes),
    )

    live_email = s.email_backend == "resend" or (s.email_backend == "auto" and bool(s.resend_api_key))
    email = (
        ResendEmailService(s.resend_api_key, s.email_from, override_to=s.demo_fallback_email)
        if live_email
        else MockEmailService()
    )

    live_tg = s.telegram_backend == "live" or (s.telegram_backend == "auto" and bool(s.telegram_bot_token))
    telegram = BotApiTelegramService(s.telegram_bot_token) if live_tg else MockTelegramService()

    return Services(calendar=calendar, routing=MockRoutingService(), email=email, telegram=telegram)
