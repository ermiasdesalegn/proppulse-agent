from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.checkpointer import open_checkpointer
from app.agent.deps import AgentDeps
from app.agent.graph import BrokerAgent
from app.api import router
from app.chat import ChatService
from app.config import get_settings
from app.db import make_pool
from app.gemini import get_gateway
from app.repository import Repository
from app.services import build_services
from app.services.telegram import BotApiTelegramService
from app.telegram_updates import TelegramPoller, TelegramUpdateHandler

log = logging.getLogger("proppulse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "Proactor" in type(asyncio.get_running_loop()).__name__:
        raise RuntimeError("psycopg cannot run on Windows' Proactor event loop. Start the server with: python -m app")
    s = get_settings()
    pool = make_pool(s)
    await pool.open(wait=True, timeout=20)
    checkpointer = await open_checkpointer(s)
    gateway = get_gateway()
    services = build_services(s)
    repo = Repository(pool)
    agent = BrokerAgent(AgentDeps(gateway=gateway, repo=repo, services=services, settings=s), checkpointer.saver)
    chat = ChatService(repo, agent, services, s)
    handler = TelegramUpdateHandler(chat)

    poller, telegram_status = None, "webhook" if s.telegram_mode == "webhook" else "off"
    if isinstance(services.telegram, BotApiTelegramService) and s.telegram_mode in ("auto", "polling"):
        try:
            poller = TelegramPoller(services.telegram, handler)
            await poller.start()
            telegram_status = "polling"
            log.info("Telegram: polling for messages")
        except Exception as exc:
            # A bad bot token must not take the web chat and API down with it.
            poller, telegram_status = None, f"unavailable ({exc})"
            log.error("Telegram is unavailable, continuing without it: %s", exc)

    app.state.settings, app.state.repo, app.state.chat = s, repo, chat
    app.state.telegram_handler, app.state.checkpointer_kind = handler, checkpointer.kind
    app.state.telegram_mode = telegram_status
    try:
        yield
    finally:
        if poller:
            await poller.stop()
        await handler.drain()
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
