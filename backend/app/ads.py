"""Telegram channel ads. Posts a listing to the broker's public channel with buttons that open a chat with the bot
already knowing which unit the buyer meant.

Every number in an ad comes from the database; the model only writes the headline and a one-line pitch.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.agent.nodes import fmt_etb
from app.config import Settings
from app.ingest.fetch import HttpFetcher
from app.repository import Repository
from app.services import Services

log = logging.getLogger(__name__)

DEEPLINK = re.compile(r"^(ask|cmp|book)_([0-9a-f]{8})$")
BUTTONS = (("ask", "Ask AI About This Unit"), ("cmp", "Compare With Similar"), ("book", "Book Site Visit"))


class AdError(Exception):
    """Safe to show to the broker."""


class AdCopy(BaseModel):
    headline: str = Field(description="At most 70 characters, no price")
    pitch: str = Field(description="One or two sentences on what makes this unit appealing, at most 200 characters, no numbers")


def property_code(property_id: UUID | str) -> str:
    return str(property_id).replace("-", "")[:8]


def deep_link(username: str, action: str, property_id: UUID | str) -> str:
    return f"https://t.me/{username}?start={action}_{property_code(property_id)}"


def keyboard(username: str, property_id: UUID | str) -> dict:
    ask, cmp_, book = (deep_link(username, a, property_id) for a, _ in BUTTONS)
    return {"inline_keyboard": [[{"text": BUTTONS[0][1], "url": ask}], [{"text": BUTTONS[1][1], "url": cmp_}, {"text": BUTTONS[2][1], "url": book}]]}


def facts_block(p: dict, *, old_price: float | None = None) -> str:
    price, sqm = float(p["price_etb"]), float(p["sqm"])
    lines = [p["title"], f"{p['developer_name']} - {p['sub_city']}"]
    lines.append(f"Price: {fmt_etb(price)} ({price / sqm:,.0f} ETB/sqm)")
    if old_price:
        lines.append(f"Was {fmt_etb(old_price)}, now {fmt_etb(price)} ({(price - old_price) / old_price * 100:.1f}%)")
    extras = []
    if p.get("down_payment_pct") is not None:
        pct = float(p["down_payment_pct"])
        extras.append(f"Down payment: {pct:g}% ({fmt_etb(price * pct / 100)})")
    if isinstance(p.get("delivery_date"), date):
        extras.append(f"Delivery: {p['delivery_date']:%b %Y}")
    if extras:
        lines.append(" | ".join(extras))
    tags = (p.get("vision_amenities") or {}).get("tags") or []
    if tags:
        lines.append("Features: " + ", ".join(tags[:6]))
    return "\n".join(lines)


class AdService:
    def __init__(self, repo: Repository, gateway: Any, services: Services, settings: Settings, events: Any = None):
        self.repo, self.gateway, self.services, self.settings, self.events = repo, gateway, services, settings, events
        self._username: str | None = None
        self.fetcher: Any = HttpFetcher()  # replaced in tests

    async def bot_username(self) -> str:
        if self._username is None:
            try:
                self._username = (await self.services.telegram.get_me())["username"]
            except Exception:
                raise AdError("Could not read the bot's username from Telegram, so the ad buttons cannot be built.") from None
        return self._username

    async def compose(self, p: dict, *, kind: str, old_price: float | None = None) -> str:
        tags = (p.get("vision_amenities") or {}).get("tags") or []
        prompt = (f"Write a short Telegram ad for this unit. Kind: {kind.replace('_', ' ').lower()}. Unit: {p['title']} by {p['developer_name']} in {p['sub_city']}, "
                  f"{p['bedrooms']} bedrooms. Features: {', '.join(tags) or 'none listed'}. Description: {p.get('description') or 'none'}.")
        try:
            copy = await self.gateway.generate_structured(prompt, AdCopy, system="You write honest, concrete property ads for an Addis Ababa broker. No hype, no invented facts.")
            headline, pitch = copy.headline.strip()[:90], copy.pitch.strip()[:260]
        except Exception:
            log.exception("ad copy generation failed; using the plain wording")
            headline = ("Price drop: " if kind == "PRICE_DROP" else "New listing: " if kind == "NEW_LISTING" else "") + f"{p['title']}"
            pitch = (p.get("description") or "").split(".")[0].strip()
        parts = [headline, "", pitch, "", facts_block(p, old_price=old_price)] if pitch else [headline, "", facts_block(p, old_price=old_price)]
        return "\n".join(parts)[:3900]

    async def publish(self, property_id: UUID | str, *, kind: str = "MANUAL", dry_run: bool = True, old_price: float | None = None) -> dict:
        rows = await self.repo.get_properties([property_id])
        if not rows:
            raise LookupError(f"property {property_id} not found")
        p = rows[0]
        if p["status"] != "AVAILABLE":
            raise AdError("That unit is not available, so it will not be advertised.")
        channel = self.settings.telegram_channel_id
        if not channel:
            raise AdError("TELEGRAM_CHANNEL_ID is not set, so there is nowhere to post.")
        username = await self.bot_username()
        text = await self.compose(p, kind=kind, old_price=old_price)
        markup = keyboard(username, p["id"])
        if dry_run:
            return {"posted": False, "preview": text, "buttons": [b["text"] for row in markup["inline_keyboard"] for b in row], "has_photo": bool(p.get("floor_plan_image_url")), "photo_url": p.get("floor_plan_image_url")}

        claim = await self.repo.claim_channel_post(property_id=p["id"], kind=kind, price_etb=float(p["price_etb"]), chat_id=channel, text=text)
        if claim is None:
            return {"posted": False, "reason": "This exact announcement was already posted."}
        try:
            message_id = None
            if p.get("floor_plan_image_url"):
                try:
                    message_id = await self.services.telegram.send_photo(channel, p["floor_plan_image_url"], text, reply_markup=markup)
                except Exception:
                    # Telegram could not fetch the address itself (hotlink protection, a private address): download the picture here and upload it.
                    try:
                        data, _ = await self.fetcher.get_bytes(p["floor_plan_image_url"])
                        message_id = await self.services.telegram.send_photo_bytes(channel, data, text, reply_markup=markup)
                    except Exception:
                        log.warning("the picture could not be posted; posting the text only")
            if message_id is None:
                message_id = await self.services.telegram.send_message(channel, text, reply_markup=markup)
        except Exception as exc:
            await self.repo.release_channel_post(claim["id"])
            log.exception("channel post failed")
            raise AdError(f"Telegram refused the post ({type(exc).__name__}). Is the bot an admin of the channel?") from None
        await self.repo.set_channel_post_message(claim["id"], message_id)
        if self.events:
            self.events.publish("ad", property_id=str(p["id"]), what=kind, title=p["title"])
        return {"posted": True, "message_id": message_id, "text": text}

    async def announce_report(self, report: dict) -> list[dict]:
        """Announce the new units and price cuts from an ingest run. One failure never stops the others."""
        results: list[dict] = []
        events = [(c["property_id"], "NEW_LISTING", None, c["title"]) for c in report.get("created", []) if c.get("property_id")]
        events += [(c["property_id"], "PRICE_DROP", c["old_price_etb"], c["title"]) for c in report.get("price_changed", [])
                   if c.get("property_id") and c["change_pct"] < 0]
        for property_id, kind, old_price, title in events:
            try:
                results.append({"title": title, "kind": kind, **await self.publish(property_id, kind=kind, dry_run=False, old_price=old_price)})
            except (AdError, LookupError) as exc:
                results.append({"title": title, "kind": kind, "posted": False, "error": str(exc)})
        return results
