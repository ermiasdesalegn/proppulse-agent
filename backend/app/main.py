from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ads import AdService
from app.agent.checkpointer import open_checkpointer
from app.agent.deps import AgentDeps
from app.agent.graph import BrokerAgent
from app.api import router
from app.chat import ChatService
from app.config import get_settings
from app.db import make_pool
from app.calllog import ModelCallLog
from app.events import DurableEventBus
from app.ingest.browser import BrowserFetcher, Politeness
from app.tracing import trace_repo, trace_services
from app.gemini import get_gateway
from app.ingest.fetch import HttpFetcher
from app.ingest.service import IngestionService
from app.proactive import ProactiveService
from app.repository import Repository
from app.services import build_services
from app.services.telegram import BotApiTelegramService
from app.telegram_updates import TelegramPoller, TelegramUpdateHandler

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
    pool = make_pool(s)
    await pool.open(wait=True, timeout=20)
    checkpointer = await open_checkpointer(s)
    gateway = get_gateway()
    real_services = build_services(s)
    repo = Repository(pool)
    events = DurableEventBus(pool, retention_days=30)
    await events.start()
    calls = ModelCallLog(pool, events)
    await calls.start()
    gateway.recorder = calls.record
    services = trace_services(real_services, events)  # every calendar, routing, email and Telegram call reports itself to the inspector
    agent = BrokerAgent(AgentDeps(gateway=gateway, repo=trace_repo(repo, events), services=services, settings=s, events=events), checkpointer.saver)
    chat = ChatService(repo, agent, services, s, events=events)
    proactive = ProactiveService(repo, agent, services, s, events=events)
    http_fetcher = HttpFetcher(allow_private=s.scrape_allow_private_hosts)
    politeness = Politeness(lambda u: http_fetcher.get_text(u, polite=False), min_interval=s.scrape_min_interval_seconds) if s.scrape_polite else None
    http_fetcher.politeness = politeness
    browser_fetcher = BrowserFetcher(http=http_fetcher, allow_private=s.scrape_allow_private_hosts, politeness=politeness)  # starts Chromium on first use
    ingest = IngestionService(repo, gateway, http_fetcher, s, events=events, browser=browser_fetcher)
    ads = AdService(repo, gateway, services, s, events=events)
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
    app.state.events, app.state.ingest, app.state.ads = events, ingest, ads
    app.state.telegram_handler, app.state.checkpointer_kind = handler, checkpointer.kind
    app.state.telegram_mode = telegram_status
    try:
        yield
    finally:
        if poller:
            await poller.stop()
        await handler.drain()
        await browser_fetcher.aclose()
        await calls.stop()
        await events.stop()
        await gateway.aclose()
        await checkpointer.aclose()
        await pool.close()


def create_app(*, use_lifespan: bool = True) -> FastAPI:
    s = get_settings()
    app = FastAPI(title="PropPulse AI", lifespan=lifespan if use_lifespan else None)
    app.add_middleware(
        CORSMiddleware, allow_origins=[s.frontend_origin], allow_methods=["GET", "POST"], allow_headers=["*"]
    )
    app.include_router(router)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "gemini_model": s.gemini_model,
            "gemini_key_configured": bool(s.gemini_api_key),
            "database_configured": bool(s.database_url),
            "checkpointer": getattr(app.state, "checkpointer_kind", None),
            "telegram": getattr(app.state, "telegram_mode", None),
        }

    return app


app = create_app()
