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
    gemini_api_keys: str = ""  # more keys, comma separated: when every model on one key is out of quota, the next key is used

    gemini_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    gemini_rpm: int = Field(14, description="Leaky-bucket ceiling for Gemini calls per minute")
    # Tried in order when the primary is overloaded, slow or out of daily quota (the free tier gives gemini-3.8-flash only 20 a day).
    gemini_fallback_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"  # measured: 1.3 s and 4-8 s per call
    gemini_thinking_budget: int = 0  # 0 = no hidden reasoning (replies in seconds, not tens of seconds); -1 = model default
    gemini_mode: str = "live"  # live | record (live, and keep every answer) | replay (recordings only, no network)
    gemini_recording_path: str = "evals/recordings/gemini.jsonl"
    gemini_primary_timeout_seconds: float = 10.0  # the primary answers in about 2 s; a slower one is skipped for a while
    gemini_timeout_seconds: float = 60.0  # whole request, including the fallback attempt
    gemini_embed_rpm: int = 60  # embeddings have their own, higher quota, so they do not queue behind text calls

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    deal_documents_bucket: str = ""  # a PRIVATE Supabase Storage bucket for contracts and receipts; needs SUPABASE_URL and the service role key
    deal_documents_dir: str = ""  # development only: keep documents in this folder instead (never set it on a host whose disk is lost on restart)
    supabase_jwt_secret: str = ""  # only for projects that still sign tokens with the shared HS256 secret; newer ones are checked against the published public keys
    database_url: str = ""

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    ingest_webhook_secret: str = ""  # shared secret for the inbound announcement email webhook
    telegram_mode: str = "auto"  # auto/polling: fetch updates (no public URL needed); webhook: Telegram calls us; off
    public_base_url: str = ""  # https URL used only by scripts/telegram_webhook.py
    resend_api_key: str = ""
    email_from: str = ""

    admin_api_key: str = ""
    secrets_master_key: str = ""  # 32 random bytes, base64: encrypts each agency's bot token and calendar key (python -m scripts.make_master_key)
    secrets_master_keys: str = ""  # during a rotation: several keys, comma separated, newest first (docs/SECURITY.md); wins over the single one
    tenancy_strict: bool = False  # True: database work with no organization in scope is an error instead of using the legacy one
    # US dollars per million tokens, "model=input/output", comma separated; `default` prices any model not listed. These are ESTIMATES the
    # operator must check against the provider's price list and update (with the date) when it changes: spend is only as good as this line.
    model_prices: str = ("default=0.30/2.50,gemini-2.5-flash=0.30/2.50,gemini-3.5-flash-lite=0.10/0.40,"
                         "gemini-3.1-flash-lite=0.10/0.40,gemini-embedding-001=0.15/0")
    model_prices_as_of: str = "2026-11-02 (estimate, not checked against the provider's list)"
    enforce_quotas: bool = True  # each agency's plan caps how many buyer messages the assistant answers per day (app/quota.py)
    cron_secret: str = ""
    job_workers: int = 2  # background job tasks in this process (app/jobs.py); 0 turns the worker off (the tests do)
    job_per_org_limit: int = 1  # jobs of one agency that may run at the same time, so one agency cannot take every worker
    job_lease_seconds: int = 120  # a running job whose worker stops extending this is handed to another worker
    frontend_origin: str = "http://localhost:3000"

    demo_fallback_email: str = ""
    demo_fallback_telegram_chat_id: str = ""

    timezone: str = "Africa/Addis_Ababa"
    business_hours_start: int = 9
    business_hours_end: int = 18
    working_days: str = "Mon,Tue,Wed,Thu,Fri,Sat"
    tour_duration_minutes: int = 60
    consultation_minutes: int = 30  # a phone call, not a visit
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

    project_crawl_max_pages: int = 80  # pages read when crawling one developer's site for projects (its sitemap seeds the list)
    project_crawl_max_model_calls: int = 30  # model calls per crawl; pages already read and unchanged cost nothing on the next run
    project_price_request_email: bool = False  # also email the developer's sales office when a buyer asks a price (off: only the broker is told)
    market_sources: str = ""  # "slug=https://site/page;slug=https://other/" replaces the built-in list of developer sites the market sweep reads
    project_photo_max_mb: int = 8  # Telegram accepts photos up to 10 MB and videos up to 50 MB; uploads from the dashboard are kept below that
    project_video_max_mb: int = 20
    scrape_polite: bool = True  # obey robots.txt and wait between requests to the same site
    scrape_min_interval_seconds: float = 2.0
    scrape_failures_before_alert: int = 3
    scrape_allow_private_hosts: bool = False  # only for pointing the scraper at a page on this machine while developing
    ingest_max_vision_per_run: int = 10  # floor-plan images read per run; each costs a model call
    github_token: str = ""  # fine-grained token: Actions read and write on ONE repository; lets the dashboard start the portal crawl
    github_repo: str = ""  # owner/name, for example ermiasdesalegn/proppulse-agent
    github_workflow: str = "portal-crawl.yml"
    github_ref: str = "main"
    portal_min_success_rate: float = 0.7  # below this share of pages priced locally the broker is told the site layout probably changed
    portal_alert_min_pages: int = 10  # ...but only after at least this many pages, so a small run does not cry wolf
    ads_auto_publish: bool = False  # post new listings and price drops to the channel without being asked
    telegram_channel_id: str = ""

    audit_export_dir: str = ""  # copy each agency's audit trail here (development); see app/auditchain.py. Off when both this and the bucket are empty
    audit_export_bucket: str = ""  # or to this S3-compatible bucket, which should have object lock with a default retention (production)
    audit_export_prefix: str = ""
    audit_export_endpoint_url: str = ""  # for an S3-compatible service other than AWS

    demo_reset_enabled: bool = False  # lets the dashboard's Reset Demo button wipe conversations and restore the demo catalogue
    history_compact_after: int = 40  # a conversation longer than this many messages has its older part summarised
    history_keep_recent: int = 12
    mortgage_rate_pct: float = 12.0  # assumptions for the affordability estimate, not an offer from any bank
    mortgage_years: int = 20
    mortgage_financed_pct: float = 70.0
    log_format: str = "text"  # text for a terminal, json for a log service such as Cloud Logging
    trust_proxy_headers: bool = False  # true only behind a proxy you control (Cloud Run): then X-Forwarded-For names the client
    rate_limit_public_per_minute: int = 120  # webhooks and the demo page, per client
    rate_limit_failed_auth_per_minute: int = 10  # rejected admin keys, per client; past this the client is blocked for a minute
    broker_name: str = "Ermias Desalegn"
    broker_telegram_chat_id: str = ""  # where the broker's own notices go; falls back to DEMO_FALLBACK_TELEGRAM_CHAT_ID
    proactive_lookback_hours: int = 24  # how far back price drops and new listings count as news
    tour_reminder_window_hours: int = 24
    commission_overdue_days: int = 14  # a commission still pending after this many days is flagged and the agency's owner is told
    milestone_notice_days: int = 3  # remind the buyer this many days before a payment milestone, and again on the day
    max_alerts_per_buyer_per_day: int = 1
    max_properties_per_alert: int = 3
    relax_budget_step_pct: float = 12.0  # step 1 widens by this, step 2 by twice this
    max_relaxation_steps: int = 2
    recursion_limit: int = 10

    high_value_threshold_etb: float = 20_000_000
    similarity_threshold: float = 0.64  # tuned on seed data: negatives <= 0.606, positives >= 0.666


    @property
    def master_key_list(self) -> list[str]:
        """The master keys, newest first: SECRETS_MASTER_KEYS, then SECRETS_MASTER_KEY if it is not among them (so leaving the old single
        variable in place during a rotation keeps it loaded, as the older key)."""
        keys = [*self.secrets_master_keys.replace("\n", ",").split(","), self.secrets_master_key]
        return list(dict.fromkeys(k.strip().strip('"').strip("'") for k in keys if k.strip()))

    @property
    def gemini_key_list(self) -> list[str]:
        """Every configured key, in order of use, without repeats: GEMINI_API_KEY first, then GEMINI_API_KEYS."""
        keys = [self.gemini_api_key, *self.gemini_api_keys.replace("\n", ",").split(",")]
        return list(dict.fromkeys(k.strip().strip('"').strip("'") for k in keys if k.strip()))

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
