"""Shared setup for the evaluations.

Every evaluation runs against a database whose changes are rolled back, with mock email/calendar/Telegram, a fixed clock and a model
that is either replayed from a recording (the default: free, instant, repeatable) or recorded live. Replay answers are the model's
real earlier answers, so a replay run measures the code around the model; a record run measures the model itself.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import MemorySaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

import app  # noqa: F401  (picks the event loop psycopg needs on Windows)
from app.agent.deps import AgentDeps
from app.agent.graph import BrokerAgent
from app.agent.nodes import AgentNodes
from app.chat import ChatService
from app.config import Settings, get_settings
from app.events import EventBus
from app.gemini import GeminiGateway, GenAIBackend
from app.gemini_replay import RecordingBackend, RecordingStore, ReplayBackend
from app.proactive import ProactiveService
from app.repository import Repository
from app.services import Services
from app.services.calendar import MockCalendarService
from app.services.email import MockEmailService
from app.services.routing import MockRoutingService
from app.services.telegram import MockTelegramService

HERE = Path(__file__).resolve().parent
RECORDING = HERE / "recordings" / "gemini.jsonl"
TZ = ZoneInfo("Africa/Addis_Ababa")
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)  # a Monday morning; prompts contain the time, so it must never move
BROKER_CHAT = "999000"
VOICE_BYTES = {"voice-eval": b"OggS-stand-in-audio"}


class OneConnection:
    """Stands in for the pool so every repository call of one evaluation shares a single connection that is rolled back at the end."""

    def __init__(self, conn: AsyncConnection):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


@dataclass
class Env:
    mode: str
    conn: AsyncConnection
    repo: Repository
    gateway: GeminiGateway
    backend: Any
    services: Services
    settings: Settings
    events: EventBus
    agent: BrokerAgent
    nodes: AgentNodes
    chat: ChatService
    deps: AgentDeps

    @property
    def live_calls(self) -> int:
        return getattr(self.backend, "live_calls", 0)

    def proactive(self, now=None) -> ProactiveService:
        return ProactiveService(self.repo, self.agent, self.services, self.settings, events=self.events, now=now or (lambda: NOW))

    async def new_buyer(self, tag: str) -> dict:
        return await self.repo.get_or_create_buyer(f"eval:{tag}")


def database_url() -> str:
    if explicit := os.environ.get("EVAL_DATABASE_URL"):
        return explicit
    from tests.localdb import local_database_url  # an embedded Postgres, migrated and seeded with the demo catalogue

    return local_database_url()


class PacedBackend:
    """Keeps a live backend inside the free tier's requests-per-minute. Only requests that really reach the model pass through it, so
    answers served from the recording are not slowed down."""

    def __init__(self, inner, generate_rpm: int, embed_rpm: int):
        self._inner = inner
        self._gap = {"generate": 60.0 / generate_rpm, "embed": 60.0 / embed_rpm}
        self._next = {"generate": 0.0, "embed": 0.0}

    async def _wait(self, lane: str) -> None:
        now = time.monotonic()
        slot = max(self._next[lane], now)
        self._next[lane] = slot + self._gap[lane]
        if slot > now:
            await asyncio.sleep(slot - now)

    async def generate_text(self, *a):
        await self._wait("generate")
        return await self._inner.generate_text(*a)

    async def generate_structured(self, *a):
        await self._wait("generate")
        return await self._inner.generate_structured(*a)

    async def transcribe_audio(self, *a):
        await self._wait("generate")
        return await self._inner.transcribe_audio(*a)

    async def describe_image(self, *a):
        await self._wait("generate")
        return await self._inner.describe_image(*a)

    async def embed(self, *a):
        await self._wait("embed")
        return await self._inner.embed(*a)


def make_backend(mode: str, settings: Settings, recording: Path):
    store = RecordingStore(recording)
    if mode == "replay":
        return ReplayBackend(store)
    live = GenAIBackend(
        settings.gemini_api_key, settings.gemini_model, settings.gemini_embed_model,
        fallback_models=settings.fallback_models, primary_timeout=settings.gemini_primary_timeout_seconds, thinking_budget=settings.gemini_thinking_budget,
    )
    return RecordingBackend(PacedBackend(live, settings.gemini_rpm, settings.gemini_embed_rpm), store)


async def open_env(mode: str = "replay", *, recording: Path = RECORDING, url: str | None = None, **overrides: Any) -> Env:
    if mode not in ("replay", "record"):
        raise ValueError("mode must be replay or record")
    settings = get_settings().model_copy(update={
        "broker_telegram_chat_id": BROKER_CHAT, "demo_fallback_telegram_chat_id": BROKER_CHAT, "demo_fallback_email": "", "telegram_channel_id": "",
        "timezone": "Africa/Addis_Ababa", **overrides,
    })
    conn = await AsyncConnection.connect(url or database_url(), row_factory=dict_row, autocommit=False, prepare_threshold=None)
    repo = Repository(OneConnection(conn))
    backend = make_backend(mode, settings, recording)
    # Pacing happens inside the backend, on live requests only; the gateway must not slow down answers read from the recording.
    gateway = GeminiGateway(backend, rpm=6000, embed_rpm=6000, request_timeout=settings.gemini_timeout_seconds)
    services = Services(calendar=MockCalendarService(), routing=MockRoutingService(), email=MockEmailService(), telegram=MockTelegramService(files=VOICE_BYTES))
    events = EventBus(history=5000)
    deps = AgentDeps(gateway=gateway, repo=repo, services=services, settings=settings, now=lambda: NOW, events=events)
    agent = BrokerAgent(deps, MemorySaver())
    return Env(mode=mode, conn=conn, repo=repo, gateway=gateway, backend=backend, services=services, settings=settings, events=events, agent=agent,
               nodes=AgentNodes(deps), chat=ChatService(repo, agent, services, settings, events=events), deps=deps)


async def close_env(env: Env) -> None:
    await env.gateway.aclose()
    await env.conn.rollback()  # nothing an evaluation did is kept
    await env.conn.close()
