from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import MemorySaver

from app.agent.deps import AgentDeps
from app.agent.graph import BrokerAgent
from app.agent.schemas import Extraction, Recommendation
from app.config import get_settings
from app.repository import Repository
from app.services import Services
from app.services.calendar import MockCalendarService
from app.services.email import MockEmailService
from app.services.routing import MockRoutingService
from app.services.telegram import MockTelegramService

TZ = ZoneInfo("Africa/Addis_Ababa")
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)  # a Monday morning


def ext(intent: str = "search", **kw) -> Extraction:
    return Extraction(intent=intent, **kw)


class FakeGateway:
    """Scripted stand-in for GeminiGateway: one extraction is consumed per router call."""

    def __init__(self, extractions=None, vector=None, transcript="", fail_extraction=False):
        self.extractions = list(extractions or [])
        self.vector = vector or [0.1] * 768
        self.transcript = transcript
        self.fail_extraction = fail_extraction
        self.calls: list[tuple[str, str]] = []
        self.audio: list[bytes] = []

    async def generate_structured(self, prompt, schema, *, system=None):
        if schema is Recommendation:
            self.calls.append(("recommendation", prompt))
            n = int(re.search(r"Number of options: (\d+)", prompt).group(1))
            return Recommendation(intro="INTRO", blurbs=[f"blurb {i}" for i in range(1, n + 1)], closing="CLOSING")
        self.calls.append(("structured", prompt))
        if self.fail_extraction:
            raise RuntimeError("gemini down")
        return self.extractions.pop(0)

    async def generate_text(self, prompt, *, system=None):
        self.calls.append(("text", prompt))
        return "LLM-REPLY"

    async def transcribe_audio(self, audio, *, mime_type="audio/ogg", prompt):
        self.calls.append(("audio", prompt))
        self.audio.append(audio)
        return self.transcript

    async def embed_query(self, text):
        self.calls.append(("embed", text))
        return self.vector

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def prompts(self, kind: str) -> list[str]:
        return [p for k, p in self.calls if k == kind]


class SpyRepo(Repository):
    def __init__(self, pool):
        super().__init__(pool)
        self.searches: list[dict] = []

    async def search_properties(self, embedding, **kw):
        self.searches.append(kw)
        return await super().search_properties(embedding, **kw)


def make_services(files=None, email=None) -> Services:
    return Services(
        calendar=MockCalendarService(), routing=MockRoutingService(),
        email=email or MockEmailService(), telegram=MockTelegramService(files=files or {}),
    )


def make_agent(repo, gateway, *, services=None, checkpointer=None, **settings_overrides) -> BrokerAgent:
    settings = get_settings().model_copy(update=settings_overrides)
    deps = AgentDeps(gateway=gateway, repo=repo, services=services or make_services(), settings=settings, now=lambda: NOW)
    return BrokerAgent(deps, checkpointer or MemorySaver())


def make_chat(repo, gateway, *, services=None, **settings_overrides):
    from app.chat import ChatService

    settings = get_settings().model_copy(update=settings_overrides)
    services = services or make_services()
    deps = AgentDeps(gateway=gateway, repo=repo, services=services, settings=settings, now=lambda: NOW)
    agent = BrokerAgent(deps, MemorySaver())
    return ChatService(repo, agent, services, settings), services, agent


def make_proactive(repo, *, services=None, now=None, **settings_overrides):
    from app.proactive import ProactiveService

    settings = get_settings().model_copy(update=settings_overrides)
    services = services or make_services()
    deps = AgentDeps(gateway=FakeGateway(), repo=repo, services=services, settings=settings, now=lambda: NOW)
    agent = BrokerAgent(deps, MemorySaver())
    return ProactiveService(repo, agent, services, settings, now=now), services, agent


async def new_buyer(repo, tag: str) -> dict:
    return await repo.get_or_create_buyer(f"test:{tag}")


async def say_to(agent, buyer: dict, text: str | None = None, *, voice_file: str | None = None):
    inbound = {"kind": "voice", "file_id": voice_file} if voice_file else {"kind": "text", "text": text}
    return await agent.handle(buyer_id=str(buyer["id"]), thread_id=buyer["thread_id"], inbound=inbound)
