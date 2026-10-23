from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.repository import Repository
from app.services import Services


@dataclass
class AgentDeps:
    gateway: Any  # GeminiGateway, or a test double with the same methods
    repo: Repository
    services: Services
    settings: Settings
    now: Callable[[], datetime] = field(default=None)  # type: ignore[assignment]
    events: Any = None  # EventBus for the live dashboard; optional

    def __post_init__(self) -> None:
        if self.now is None:
            tz = ZoneInfo(self.settings.timezone)
            self.now = lambda: datetime.now(tz)
