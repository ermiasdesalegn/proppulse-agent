from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool import PoolTimeout

from app.ads import AdService
from app.agent.checkpointer import open_checkpointer
from app.agent.deps import AgentDeps
from app.agent.graph import BrokerAgent
from app.api import router
from app.calllog import ModelCallLog
from app.channels_api import router as channels_router
from app.chat import ChatService
from app.commission_api import router as commission_router
from app.config import Settings, get_settings
from app.db import make_pool
from app.documents_api import router as documents_router
from app.events import DurableEventBus
from app.gemini import get_gateway
from app.ingest.browser import BrowserFetcher, Politeness
from app.ingest.fetch import HttpFetcher
from app.ingest.service import IngestionService
from app.jobs import JobQueue, JobWorker
from app.jobs_api import router as jobs_router
from app.keys import check_keys
from app.observability import GuardMiddleware, secrets_from
from app.operator_api import router as operator_router
from app.org_api import router as org_router
from app.portal_api import router as portal_router
from app.prices_api import router as prices_router
from app.proactive import ProactiveService
from app.projects import ProjectService
from app.quota import router as quota_router
from app.repository import Repository
from app.runtime import TenantRuntime
from app.services import build_services
from app.services.telegram import BotApiTelegramService
from app.telegram_updates import TelegramPoller, TelegramUpdateHandler
from app.terms_api import router as terms_router
from app.tracing import trace_repo, trace_services
from app.vault import Vault

log = logging.getLogger("proppulse")


async def register_webhook(s, telegram) -> str:
    """Behind a platform that scales to zero or runs several copies polling cannot work, so Telegram is told where to call."""
    try:
        if not (s.public_base_url.startswith("https://") and s.telegram_webhook_secret):
            raise ValueError("PUBLIC_BASE_URL (https) and TELEGRAM_WEBHOOK_SECRET are required in webhook mode")
        await telegram.set_webhook(f"{s.public_base_url.rstrip('/')}/api/telegram/webhook", s.telegram_webhook_secret)
        log.info("Telegram: webhook registered at %s", s.public_base_url)
        return "webhook"
    except Exception as exc:
        log.error("Telegram webhook could not be registered: %s", exc)
        return f"unavailable ({exc})"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "Proactor" in type(asyncio.get_running_loop()).__name__:
        raise RuntimeError("psycopg cannot run on Windows' Proactor event loop. Start the server with: python -m app")
    s = get_settings()
    for attempt in (1, 2, 3):  # a hosted database can be slow to answer; a failed wait closes its pool, so each try uses a fresh one
        pool = make_pool(s)
        try:
            await pool.open(wait=True, timeout=20)
            break
        except PoolTimeout:
            if attempt == 3:
                raise RuntimeError("The database did not answer in 60 seconds. Check DATABASE_URL and your internet connection, then start again.") from None
            log.warning("the database is slow to answer (attempt %d of 3), still trying", attempt)
    checkpointer = await open_checkpointer(s)
    gateway = get_gateway()
    real_services = build_services(s)
    repo = Repository(pool, strict=s.tenancy_strict)
    events = DurableEventBus(pool, retention_days=30)
    await events.start()
    calls = ModelCallLog(pool, events)
    await calls.start()
    gateway.recorder = calls.record
    vault = Vault(s.master_key_list) if s.master_key_list else None
    keys = await check_keys(pool, vault)  # refuses to start if an agency's data key names a master key this server was not given
    if vault is not None and not keys["done"]:
        log.warning("master-key rotation in progress: %s (python -m scripts.rotate_master_key --status)", keys["by_key"])
    runtime = TenantRuntime(repo, s, real_services, vault)
    ts = runtime.tenant_settings()  # the global settings, except the few values that belong to the agency in scope
    # Every calendar, routing, email and Telegram call reports itself to the inspector; each resolves to the agency in scope.
    services = trace_services(runtime.proxied_services(), events)
    agent = BrokerAgent(AgentDeps(gateway=gateway, repo=trace_repo(repo, events), services=services, settings=ts, events=events), checkpointer.saver)
    chat = ChatService(repo, agent, services, ts, events=events)
    proactive = ProactiveService(repo, agent, services, ts, events=events)
    http_fetcher = HttpFetcher(allow_private=s.scrape_allow_private_hosts)
    politeness = Politeness(lambda u: http_fetcher.get_text(u, polite=False), min_interval=s.scrape_min_interval_seconds) if s.scrape_polite else None
    http_fetcher.politeness = politeness
    browser_fetcher = BrowserFetcher(http=http_fetcher, allow_private=s.scrape_allow_private_hosts, politeness=politeness)  # starts Chromium on first use
    ingest = IngestionService(repo, gateway, http_fetcher, s, events=events, browser=browser_fetcher)
    ads = AdService(repo, gateway, services, ts, events=events)
    projects = ProjectService(repo, gateway, services, ts, fetcher=http_fetcher, proactive=proactive, ads=ads, events=events)
    chat.projects = projects
    handler = TelegramUpdateHandler(chat)

    poller, telegram_status = None, "webhook" if s.telegram_mode == "webhook" else "off"
    if s.telegram_mode == "webhook" and isinstance(real_services.telegram, BotApiTelegramService):
        telegram_status = await register_webhook(s, real_services.telegram)
    if isinstance(real_services.telegram, BotApiTelegramService) and s.telegram_mode in ("auto", "polling"):
        try:
            poller = TelegramPoller(real_services.telegram, handler)
            await poller.start()
            telegram_status = "polling"
            log.info("Telegram: polling for messages")
        except Exception as exc:
            # A bad bot token must not take the web chat and API down with it.
            poller, telegram_status = None, f"unavailable ({exc})"
            log.error("Telegram is unavailable, continuing without it: %s", exc)

    app.state.settings, app.state.repo, app.state.chat, app.state.proactive = s, repo, chat, proactive
    app.state.events, app.state.ingest, app.state.ads, app.state.projects = events, ingest, ads, projects
    app.state.telegram_handler, app.state.checkpointer_kind = handler, checkpointer.kind
    app.state.runtime = runtime
    app.state.telegram_mode = telegram_status
    app.state.jobs = JobQueue(pool, per_org_limit=s.job_per_org_limit, lease=timedelta(seconds=s.job_lease_seconds), secrets=secrets_from(s))
    projects.jobs = app.state.jobs
    worker = JobWorker(app.state.jobs, app.state, concurrency=s.job_workers) if s.job_workers > 0 else None
    if worker:
        await worker.start()
    try:
        yield
    finally:
        if worker:
            await worker.stop()
        if poller:
            await poller.stop()
        await handler.drain()
        await browser_fetcher.aclose()
        await calls.stop()
        await events.stop()
        await gateway.aclose()
        await checkpointer.aclose()
        await pool.close()


def create_app(*, use_lifespan: bool = True, settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(title="PropPulse AI", lifespan=lifespan if use_lifespan else None)
    app.add_middleware(
        CORSMiddleware, allow_origins=[o.strip().rstrip("/") for o in s.frontend_origin.split(",") if o.strip()],  # FRONTEND_ORIGIN may list several, comma separated
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["*"]
    )
    app.add_middleware(GuardMiddleware, public_per_minute=s.rate_limit_public_per_minute, failed_auth_per_minute=s.rate_limit_failed_auth_per_minute,
                       trust_proxy=s.trust_proxy_headers)
    app.include_router(router)
    app.include_router(portal_router)
    app.include_router(org_router)
    app.include_router(channels_router)
    app.include_router(quota_router)
    app.include_router(jobs_router)
    app.include_router(prices_router)
    app.include_router(terms_router)
    app.include_router(commission_router)
    app.include_router(documents_router)
    app.include_router(operator_router)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "gemini_model": s.gemini_model,
            "gemini_key_configured": bool(s.gemini_key_list), "gemini_keys": len(s.gemini_key_list),
            "database_configured": bool(s.database_url),
            "checkpointer": getattr(app.state, "checkpointer_kind", None),
            "telegram": getattr(app.state, "telegram_mode", None),
        }

    return app


app = create_app()
