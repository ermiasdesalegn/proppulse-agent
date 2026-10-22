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
    # Tried in order when the primary is overloaded, slow or out of daily quota (the free tier gives gemini-3.8-flash only 20 a day).
    gemini_fallback_models: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite"
    gemini_primary_timeout_seconds: float = 20.0
    gemini_timeout_seconds: float = 60.0  # whole request, including the fallback attempt

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_mode: str = "auto"  # auto/polling: fetch updates (no public URL needed); webhook: Telegram calls us; off
    public_base_url: str = ""  # https URL used only by scripts/telegram_webhook.py
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

    broker_name: str = "Ermias Desalegn"
    relax_budget_step_pct: float = 12.0  # step 1 widens by this, step 2 by twice this
    max_relaxation_steps: int = 2
    recursion_limit: int = 10

    high_value_threshold_etb: float = 20_000_000
    similarity_threshold: float = 0.64  # tuned on seed data: negatives <= 0.606, positives >= 0.666


    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
