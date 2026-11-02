"""An agency connects its own channels: its Telegram bot, its calendar, and how the assistant introduces it."""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.access import Principal, console_access, console_admin
from app.audit import record
from app.runtime import TenantRuntime
from app.services.telegram import TelegramError
from app.vault import VaultError

log = logging.getLogger(__name__)
router = APIRouter()

BOT_TOKEN_SHAPE = r"^\d{6,12}:[A-Za-z0-9_-]{30,60}$"  # what Telegram's BotFather hands out


class TelegramIn(BaseModel):
    bot_token: str = Field(pattern=BOT_TOKEN_SHAPE)


class CalendarIn(BaseModel):
    service_account_json: str = Field(min_length=20, max_length=20_000)
    calendar_id: str = Field(min_length=3, max_length=254)


class SettingsIn(BaseModel):
    broker_name: str | None = Field(default=None, max_length=80)
    persona: str | None = Field(default=None, max_length=500)
    broker_chat_id: str | None = Field(default=None, max_length=40, pattern=r"^-?\d{1,20}$")
    timezone: str | None = Field(default=None, max_length=60)
    listed: bool | None = None  # appear in the public directory of agencies
    brand_color: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")  # empty clears it
    logo_url: str | None = Field(default=None, max_length=300, pattern=r"^(https://[^\s<>\"']+)?$")  # an https address, or empty to clear it


def runtime_of(request: Request) -> TenantRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(503, "Per-agency channels are not available on this server")
    return runtime


def _webhook_url(request: Request, slug: str) -> str | None:
    base = request.app.state.settings.public_base_url.rstrip("/")
    return f"{base}/api/telegram/webhook/{slug}" if base.startswith("https://") else None


async def _status(request: Request, p: Principal) -> dict[str, Any]:
    runtime = runtime_of(request)
    profile = await request.app.state.repo.as_platform().org_profile(p.tenant.org_id)
    have = await runtime.secrets(p.tenant.org_id)
    return {
        "credentials_can_be_stored": runtime._vault is not None,
        "telegram": {
            "connected": "telegram_bot_token" in have, "bot_username": profile["telegram_bot_username"],
            "connected_at": profile["telegram_connected_at"], "webhook_url": _webhook_url(request, profile["slug"]),
        },
        "calendar": {"connected": "google_service_account_json" in have and bool(profile["google_calendar_id"]), "calendar_id": profile["google_calendar_id"]},
        "settings": {"broker_name": profile["broker_name"], "persona": profile["persona"], "broker_chat_id": profile["broker_chat_id"], "timezone": profile["timezone"],
                     "listed": bool(profile["listed"]), "brand_color": profile["brand_color"], "logo_url": profile["logo_url"]},
    }


@router.get("/api/channels")
async def channels(request: Request, p: Principal = Depends(console_access)):
    return await _status(request, p)


@router.put("/api/channels/telegram")
async def connect_telegram(body: TelegramIn, request: Request, p: Principal = Depends(console_admin)):
    runtime, repo = runtime_of(request), request.app.state.repo.as_platform()
    org = p.tenant.org_id
    bot = runtime.telegram_factory(body.bot_token)
    try:
        me = await bot.get_me()  # proves the token is real before anything is stored
    except TelegramError:
        raise HTTPException(422, "Telegram did not accept that bot token. Copy it again from BotFather.") from None
    secret = secrets.token_urlsafe(32)
    try:
        await runtime.store_secret(org, "telegram_bot_token", body.bot_token)
        await runtime.store_secret(org, "telegram_webhook_secret", secret)
    except VaultError as exc:
        raise HTTPException(503, str(exc)) from None
    profile = await repo.org_profile(org)
    url = _webhook_url(request, profile["slug"])
    webhook = "not registered: set PUBLIC_BASE_URL to the server's https address"
    if url:
        try:
            await bot.set_webhook(url, secret)
            webhook = "registered"
        except TelegramError as exc:
            log.warning("webhook for organization %s could not be registered: %s", org, exc)
            webhook = "could not be registered; try again"
    await repo.update_org_settings(org, telegram_bot_username=me.get("username"), telegram_connected_at=_now())
    runtime.invalidate(org)
    await record(request, p, "channel.telegram_connect", me.get("username"), webhook=webhook)
    return {**await _status(request, p), "webhook": webhook}


@router.delete("/api/channels/telegram")
async def disconnect_telegram(request: Request, p: Principal = Depends(console_admin)):
    runtime, org = runtime_of(request), p.tenant.org_id
    token = (await runtime.secrets(org)).get("telegram_bot_token")
    if token:
        try:
            await runtime.telegram_factory(token).delete_webhook()
        except (TelegramError, AttributeError):
            log.warning("webhook of organization %s could not be removed at Telegram", org)
    for name in ("telegram_bot_token", "telegram_webhook_secret"):
        await runtime.remove_secret(org, name)
    await request.app.state.repo.as_platform().update_org_settings(org, telegram_bot_username=None, telegram_connected_at=None)
    await record(request, p, "channel.telegram_disconnect")
    return await _status(request, p)


@router.put("/api/channels/calendar")
async def connect_calendar(body: CalendarIn, request: Request, p: Principal = Depends(console_admin)):
    runtime, org = runtime_of(request), p.tenant.org_id
    try:
        account = json.loads(body.service_account_json)
    except ValueError:
        raise HTTPException(422, "That is not valid JSON. Paste the whole service-account key file.") from None
    if not (isinstance(account, dict) and account.get("client_email") and account.get("private_key")):
        raise HTTPException(422, "That does not look like a Google service-account key (it needs client_email and private_key).")
    try:
        await runtime.store_secret(org, "google_service_account_json", body.service_account_json)
    except VaultError as exc:
        raise HTTPException(503, str(exc)) from None
    await request.app.state.repo.as_platform().update_org_settings(org, google_calendar_id=body.calendar_id.strip())
    runtime.invalidate(org)
    await record(request, p, "channel.calendar_connect", body.calendar_id.strip())
    return await _status(request, p)


@router.delete("/api/channels/calendar")
async def disconnect_calendar(request: Request, p: Principal = Depends(console_admin)):
    runtime, org = runtime_of(request), p.tenant.org_id
    await runtime.remove_secret(org, "google_service_account_json")
    await request.app.state.repo.as_platform().update_org_settings(org, google_calendar_id=None)
    runtime.invalidate(org)
    await record(request, p, "channel.calendar_disconnect")
    return await _status(request, p)


@router.patch("/api/org/settings")
async def change_settings(body: SettingsIn, request: Request, p: Principal = Depends(console_admin)):
    runtime, org = runtime_of(request), p.tenant.org_id
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to change")
    if tz := changes.get("timezone"):
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise HTTPException(422, "Unknown time zone. Use a name such as Africa/Addis_Ababa.") from None
    for key in ("brand_color", "logo_url"):
        if key in changes and not changes[key]:
            changes[key] = None
    if "listed" in changes and changes["listed"] is None:
        del changes["listed"]
    for key in ("broker_name", "persona"):
        if key in changes and changes[key] is not None:
            changes[key] = " ".join(changes[key].split()) or None  # one line, no stray whitespace in what reaches the prompt
    await request.app.state.repo.as_platform().update_org_settings(org, **changes)
    runtime.invalidate(org)
    await record(request, p, "settings.change", ", ".join(sorted(changes)))
    return await _status(request, p)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
