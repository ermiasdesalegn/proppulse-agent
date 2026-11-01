"""Per-organization settings and services behind the names the rest of the code already uses.

About thirty places read `settings.broker_name`, `settings.broker_telegram_chat_id`, `services.telegram`, `services.calendar` and so on.
Instead of passing an organization through every one of them, the application hands them `TenantSettings` and a `Services` whose
members are proxies: inside `runtime.scope(...)` each read or call resolves to the organization in scope, and outside it to the
original, global configuration. An organization that has not connected a channel gets a silent stand-in, never the platform's own bot
or calendar, so one agency's buyers can never be messaged, or booked, through another's.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.config import Settings
from app.repository import Repository
from app.services import Services, build_services
from app.services.telegram import BotApiTelegramService, MockTelegramService
from app.tenancy import DEFAULT_ORG_ID, TenantContext, agency_note, use_tenant
from app.vault import Vault, VaultError

log = logging.getLogger(__name__)

SECRET_NAMES = ("telegram_bot_token", "telegram_webhook_secret", "google_service_account_json")
PERSONA_LIMIT = 500


@dataclass(frozen=True)
class OrgProfile:
    org_id: UUID
    slug: str
    name: str
    status: str
    broker_name: str
    broker_chat_id: str
    timezone: str
    persona: str | None
    calendar_id: str

    @property
    def is_default(self) -> bool:
        return self.org_id == DEFAULT_ORG_ID


@dataclass(frozen=True)
class Active:
    profile: OrgProfile
    services: Services


_active: ContextVar[Active | None] = ContextVar("active_org_runtime", default=None)


def active_profile() -> OrgProfile | None:
    active = _active.get()
    return active.profile if active else None


class TenantSettings:
    """The global settings, except for the few values that belong to an agency. Everything else passes straight through."""

    _OWN = frozenset({"broker_name", "broker_telegram_chat_id", "timezone"})
    _OFF_FOR_OTHERS = frozenset({"demo_fallback_telegram_chat_id", "demo_fallback_email"})  # the demo's redirects are the platform owner's, not an agency's

    def __init__(self, base: Settings):
        object.__setattr__(self, "_base", base)

    def __getattr__(self, name: str) -> Any:
        base = object.__getattribute__(self, "_base")
        profile = active_profile()
        if profile is not None:
            if name in self._OWN:
                return {"broker_name": profile.broker_name, "broker_telegram_chat_id": profile.broker_chat_id, "timezone": profile.timezone}[name]
            if name in self._OFF_FOR_OTHERS and not profile.is_default:
                return ""
        return getattr(base, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("settings are read-only")


class ServiceProxy:
    """Stands in for one service (the Telegram bot, the calendar...) and forwards every call to the one for the organization in scope."""

    def __init__(self, member: str, base: Any):
        self._member, self._base = member, base

    def __getattr__(self, attr: str) -> Any:
        active = _active.get()
        target = getattr(active.services, self._member) if active else self._base
        return getattr(target, attr)


class TenantRuntime:
    def __init__(
        self, repo: Repository, settings: Settings, base_services: Services, vault: Vault | None = None, *,
        ttl: float = 60.0, clock: Any = time.monotonic, telegram_factory: Callable[[str], Any] = BotApiTelegramService,
    ):
        self._repo = repo.as_platform()  # reads the tenancy tables, which an organization's own role may not
        self.settings, self._base, self._vault = settings, base_services, vault
        self.telegram_factory = telegram_factory  # how a bot client is made from a token; tests replace it so nothing reaches Telegram
        self._ttl, self._clock = ttl, clock
        self._cache: dict[UUID, tuple[float, Active]] = {}

    # ---- what the application is wired with ----

    def tenant_settings(self) -> TenantSettings:
        return TenantSettings(self.settings)

    def proxied_services(self) -> Services:
        b = self._base
        return Services(
            calendar=ServiceProxy("calendar", b.calendar), routing=ServiceProxy("routing", b.routing),
            email=ServiceProxy("email", b.email), telegram=ServiceProxy("telegram", b.telegram),
        )  # type: ignore[arg-type]

    # ---- entering an organization ----

    @asynccontextmanager
    async def scope(self, org: TenantContext | UUID | str) -> AsyncIterator[TenantContext]:
        """Run the enclosed work as one organization: its database rows, its settings and its channels. Tasks started inside inherit it."""
        tenant = org if isinstance(org, TenantContext) else TenantContext(org_id=UUID(str(org)))
        active = await self.activate(tenant.org_id)
        token = _active.set(active)
        try:
            with use_tenant(tenant), agency_note(self._note(active.profile)):
                yield tenant
        finally:
            _active.reset(token)

    @staticmethod
    def _note(profile: OrgProfile) -> str:
        persona = f" {profile.persona.strip()[:PERSONA_LIMIT]}" if profile.persona else ""
        return f" You work for {profile.broker_name}.{persona}"

    async def activate(self, org_id: UUID) -> Active:
        hit = self._cache.get(org_id)
        if hit and hit[0] > self._clock():
            return hit[1]
        profile = await self._load_profile(org_id)
        active = Active(profile, await self._build_services(profile))
        self._cache[org_id] = (self._clock() + self._ttl, active)
        return active

    def invalidate(self, org_id: UUID | str) -> None:
        self._cache.pop(UUID(str(org_id)), None)

    # ---- loading ----

    async def _load_profile(self, org_id: UUID) -> OrgProfile:
        row = await self._repo.org_profile(org_id)
        if row is None:
            raise LookupError(f"no organization {org_id}")
        s = self.settings
        default = org_id == DEFAULT_ORG_ID
        broker = row["broker_name"] or (s.broker_name if default else (row["display_name"] or row["name"]))
        chat = row["broker_chat_id"] or (s.broker_telegram_chat_id if default else "")
        return OrgProfile(
            org_id=org_id, slug=row["slug"], name=row["name"], status=row["status"], broker_name=broker, broker_chat_id=chat or "",
            timezone=row["timezone"] or s.timezone, persona=row["persona"], calendar_id=row["google_calendar_id"] or "",
        )

    async def secrets(self, org_id: UUID) -> dict[str, str]:
        """The organization's decrypted credentials. A value that cannot be opened is treated as not connected, never as an error for others."""
        if self._vault is None:
            return {}
        wrapped = await self._repo.org_key(org_id)
        rows = await self._repo.org_secret_rows(org_id) if wrapped else []
        if not rows:
            return {}
        try:
            key = self._vault.open_data_key(org_id, wrapped)  # type: ignore[arg-type]
        except VaultError:
            log.error("the data key of organization %s could not be opened; its channels are off", org_id)
            return {}
        out: dict[str, str] = {}
        for r in rows:
            try:
                out[r["name"]] = self._vault.open(key, org_id, r["name"], r["ciphertext"])
            except VaultError:
                log.error("secret %s of organization %s could not be opened; treated as not connected", r["name"], org_id)
        return out

    async def store_secret(self, org_id: UUID | str, name: str, value: str) -> None:
        """Encrypt and keep one credential for an organization, creating the organization's data key the first time."""
        if name not in SECRET_NAMES:
            raise ValueError(f"unknown secret {name!r}")
        if self._vault is None:
            raise VaultError("SECRETS_MASTER_KEY is not set, so credentials cannot be stored")
        org = UUID(str(org_id))
        wrapped = await self._repo.org_key(org)
        if wrapped is None:
            _, fresh = self._vault.new_data_key(org)
            wrapped = await self._repo.ensure_org_key(org, fresh)  # if two requests race, both end up with the stored one
        key = self._vault.open_data_key(org, wrapped)
        await self._repo.put_org_secret(org, name, self._vault.seal(key, org, name, value))
        self.invalidate(org)

    async def remove_secret(self, org_id: UUID | str, name: str) -> bool:
        removed = await self._repo.delete_org_secret(UUID(str(org_id)), name)
        self.invalidate(org_id)
        return removed

    async def _build_services(self, profile: OrgProfile) -> Services:
        if profile.is_default:
            return self._base  # the original organization keeps the platform's configured channels
        secrets = await self.secrets(profile.org_id)
        token, account = secrets.get("telegram_bot_token", ""), secrets.get("google_service_account_json", "")
        mine = self.settings.model_copy(update={
            "telegram_bot_token": "", "telegram_backend": "mock",  # the bot, if there is one, is made below from the agency's own token
            "google_service_account_json": account, "google_calendar_id": profile.calendar_id,
            "calendar_backend": "google" if account and profile.calendar_id else "mock",
            "demo_fallback_email": "", "demo_fallback_telegram_chat_id": "",
        })
        try:
            services = build_services(mine)
        except Exception:  # a bad calendar key must not take the agency's chat down with it
            log.exception("channels of organization %s could not be built; using silent stand-ins", profile.org_id)
            services = build_services(mine.model_copy(update={"google_service_account_json": "", "calendar_backend": "mock"}))
        if token:
            telegram: Any = self.telegram_factory(token)
        else:
            telegram = MockTelegramService()
            for attr in ("messages", "actions", "locations", "photos", "documents", "answered"):
                setattr(telegram, attr, deque(maxlen=50))  # a stand-in that remembers only recent calls, so it cannot grow without bound
        return Services(calendar=services.calendar, routing=self._base.routing, email=services.email, telegram=telegram)
