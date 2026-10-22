from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT / "backend" / ".env",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    gemini_rpm: int = Field(14, description="Leaky-bucket ceiling for Gemini calls per minute")

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    resend_api_key: str = ""
    email_from: str = ""

    admin_api_key: str = ""
    cron_secret: str = ""
    frontend_origin: str = "http://localhost:3000"

    demo_fallback_email: str = ""
    demo_fallback_telegram_chat_id: str = ""

    timezone: str = "Africa/Addis_Ababa"
    business_hours_start: int = 9
    business_hours_end: int = 18
    working_days: str = "Mon,Tue,Wed,Thu,Fri,Sat"
    tour_duration_minutes: int = 60
    tour_buffer_minutes: int = 30

    # "mock" or the live implementation; "auto" picks live when credentials exist.
    calendar_backend: str = "mock"
    routing_backend: str = "mock"
    email_backend: str = "auto"
    telegram_backend: str = "auto"

    high_value_threshold_etb: float = 20_000_000
    similarity_threshold: float = 0.64  # tuned on seed data: negatives <= 0.606, positives >= 0.666


@lru_cache
def get_settings() -> Settings:
    return Settings()
