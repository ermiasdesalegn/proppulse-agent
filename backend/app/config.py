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
    gemini_fallback_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"  # measured: 1.3 s and 4-8 s per call
    gemini_thinking_budget: int = 0  # 0 = no hidden reasoning (replies in seconds, not tens of seconds); -1 = model default
    gemini_primary_timeout_seconds: float = 20.0
    gemini_timeout_seconds: float = 60.0  # whole request, including the fallback attempt
    gemini_embed_rpm: int = 60  # embeddings have their own, higher quota, so they do not queue behind text calls

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

    google_service_account_json: str = ""  # the key as raw JSON, or the path to the JSON file
    google_calendar_id: str = ""
    mapbox_token: str = ""
    office_lat: float | None = None  # where tours start from; leave unset to start at the first stop
    office_lng: float | None = None

    # "mock", the live implementation, or "auto" to use live whenever its credentials exist.
    calendar_backend: str = "auto"
    routing_backend: str = "auto"
    email_backend: str = "auto"
    telegram_backend: str = "auto"

    scrape_allow_private_hosts: bool = False  # only for pointing the scraper at a page on this machine while developing
    ingest_max_vision_per_run: int = 10  # floor-plan images read per run; each costs a model call
    ads_auto_publish: bool = False  # post new listings and price drops to the channel without being asked
    telegram_channel_id: str = ""

    broker_name: str = "Ermias Desalegn"
    broker_telegram_chat_id: str = ""  # where the broker's own notices go; falls back to DEMO_FALLBACK_TELEGRAM_CHAT_ID
    proactive_lookback_hours: int = 24  # how far back price drops and new listings count as news
    tour_reminder_window_hours: int = 24
    milestone_notice_days: int = 3  # remind the buyer this many days before a payment milestone, and again on the day
    max_alerts_per_buyer_per_day: int = 1
    max_properties_per_alert: int = 3
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
