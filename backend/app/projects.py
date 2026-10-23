"""Projects for developers that publish no unit prices: reading their sites, showing them to buyers, taking price requests, advertising offers.

The flow this supports:
  1. the broker crawls a developer's site (a preview first), and the projects it finds are kept with area, sizes, floors, status and any offer;
  2. a buyer who is interested taps "Ask the price" (in the bot, or on a channel ad); the broker is told and, if configured, the developer;
  3. the broker types the price in the dashboard; every buyer who asked is told at once;
  4. projects with a live offer are advertised in the channel, once per offer.

Nothing here invents a price. A project's price is empty ("price on request") until a person sets it.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from uuid import UUID

from app.config import Settings
from app.finance import fmt_etb
from app.ingest.fetch import HttpFetcher
from app.ingest.projects import crawl, read_projects
from app.language import msg
from app.repository import Repository
from app.services import Services

log = logging.getLogger(__name__)

PROJECT_LINK = re.compile(r"^(proj|pinfo)_([0-9a-f]{8})$")
ASK_PRICE = re.compile(r"^\s*Ask the price of (.{3,120}?)\s*$", re.I)
PROJECT_BUTTONS = (("proj", "Ask the price"), ("pinfo", "More about this project"))


class ProjectError(Exception):
    """Safe to show to the broker."""


def project_code(project_id: UUID | str) -> str:
    return str(project_id).replace("-", "")[:8]


def _range(low: Any, high: Any, unit: str) -> str | None:
    if low is None and high is None:
        return None
    a, b = (float(low) if low is not None else None), (float(high) if high is not None else None)
    if a is not None and b is not None and a != b:
        return f"{a:g}-{b:g} {unit}"
    return f"{(a if a is not None else b):g} {unit}"


def has_live_offer(p: dict, today: date | None = None) -> bool:
    return bool(p.get("promo_text")) and (p.get("promo_until") is None or p["promo_until"] >= (today or date.today()))


def facts(p: dict) -> list[str]:
    lines = [f"{p['name']} - {p['developer_name']}"]
    place = ", ".join(x for x in (p.get("sub_city"), p.get("location_text")) if x)
    if place:
        lines.append(place)
    parts = [_range(p.get("sqm_min"), p.get("sqm_max"), "sqm"), _range(p.get("bedrooms_min"), p.get("bedrooms_max"), "bedrooms"),
             f"{p['floors']} floors" if p.get("floors") else None]
    if any(parts):
        lines.append(" | ".join(x for x in parts if x))
    lines.append({"SELLING": "Selling now", "SOLD_OUT": "Sold out", "UPCOMING": "Coming soon"}.get(p["status"], "Selling now"))
    if has_live_offer(p):
        until = f" (until {p['promo_until']:%d %b %Y})" if p.get("promo_until") else ""
        pct = f"{float(p['promo_discount_pct']):g}%" if p.get("promo_discount_pct") else ""
        discount = f"{pct} off - " if pct and pct not in p["promo_text"] else ""  # do not say it twice when the developer's words already do
        lines.append(f"Offer: {discount}{p['promo_text']}{until}")
    if p.get("price_from_etb") is not None:
        lines.append(f"Price: from {fmt_etb(float(p['price_from_etb']))}" + (f" ({p['price_note']})" if p.get("price_note") else ""))
    else:
        lines.append("Price: on request")
    return lines


def card_text(p: dict, lang: str = "en") -> str:
    out = facts(p)
    if p.get("summary"):
        out += ["", p["summary"]]
    return "\n".join(out)


def ask_button(p: dict) -> list[dict]:
    return [{"label": msg("btn_ask_price", "en"), "text": f"Ask the price of {p['name']}"}]


async def suggest_projects(repo: Repository, prefs: dict, lang: str = "en", limit: int = 2) -> tuple[str, list[list[dict]]]:
    """Projects that fit a buyer whose search found no priced unit: the text to append and the buttons for it."""
    from app.geo import expand_with_adjacent

    cities = expand_with_adjacent(list(prefs.get("preferred_sub_cities") or []))
    found = await repo.search_projects(sub_cities=cities, min_bedrooms=prefs.get("min_bedrooms"), limit=limit)
    if not found:
        return "", []
    return "\n\n".join([msg("proj_header", lang)] + ["\n".join(facts(p)) for p in found]), [ask_button(p) for p in found]


class ProjectService:
    def __init__(self, repo: Repository, gateway: Any, services: Services, settings: Settings, *, fetcher: Any = None, proactive: Any = None,
                 ads: Any = None, events: Any = None):
        self.repo, self.gateway, self.services, self.settings = repo, gateway, services, settings
        self.fetcher = fetcher or HttpFetcher()
        self.proactive, self.ads, self.events = proactive, ads, events
        self._names: tuple[float, list[dict]] = (0.0, [])

    # ---------------- reading developer sites ----------------

    async def crawl_developer(self, slug: str, url: str | None = None, *, dry_run: bool = True) -> dict:
        developer = await self.repo.get_developer_by_slug(slug)
        if developer is None:
            raise LookupError(f"developer {slug!r} not found")
        start = (url or developer.get("website_url") or "").strip()
        if not start.startswith(("http://", "https://")):
            raise ProjectError("Give the developer's website address (it must start with https://).")
        pages, notes = await crawl(self.fetcher, start, max_pages=self.settings.project_crawl_max_pages)
        existing = {p["slug"]: p for p in await self.repo.list_projects() if p["developer_id"] == developer["id"]}
        known = {p["source_url"]: p["content_hash"] for p in existing.values()}
        rows, more = await read_projects(self.gateway, pages, known=known)
        report: dict[str, Any] = {"developer": slug, "dry_run": dry_run, "pages_read": len(pages), "created": [], "updated": [], "unchanged": [], "notes": notes + more}
        seen_names: set[str] = set()
        for row in rows:
            if row.get("unchanged"):
                report["unchanged"].append(row["source_url"])
                continue
            if row["slug"] in seen_names:  # the same project is often linked from several pages; the first (usually the shallowest) wins
                report["notes"].append(f"Skipped a duplicate of {row['name']} ({row['source_url']}).")
                continue
            seen_names.add(row["slug"])
            summary = {"name": row["name"], "area": row.get("sub_city"), "status": row["status"], "offer": row.get("promo_text"), "source_url": row["source_url"]}
            if dry_run:
                report["updated" if row["slug"] in existing else "created"].append(summary)
                continue
            saved, what = await self.repo.upsert_project(developer["id"], row)
            report[what if what != "unchanged" else "unchanged"].append(summary if what != "unchanged" else row["source_url"])
        if not dry_run and (report["created"] or report["updated"]) and self.events:
            self.events.publish("ingest", what="projects", developer=slug, created=len(report["created"]), updated=len(report["updated"]))
        if not pages:
            report["notes"].append("No page could be read. Check the address, robots.txt, or whether the site needs a browser.")
        elif not (rows or report["unchanged"]):
            report["notes"].append("No page looked like a single residential project. Try the address of the page that lists the projects.")
        return report

    # ---------------- buyers ----------------

    async def _projects(self) -> list[dict]:
        import time

        now = time.monotonic()
        if now - self._names[0] > 60:
            self._names = (now, await self.repo.list_projects(only_selling=False))
        return self._names[1]

    async def find_in_text(self, text: str) -> dict | None:
        """A project the buyer names in their message (longest name wins), or None."""
        lowered = text.lower()
        hits = [p for p in await self._projects() if len(p["name"]) >= 6 and p["name"].lower() in lowered]
        return max(hits, key=lambda p: len(p["name"])) if hits else None

    async def handle_text(self, buyer: dict, text: str, lang: str = "en") -> tuple[str, list[list[dict]]] | None:
        """Buttons and project names are understood in code. None means this message is not about a project."""
        ask = ASK_PRICE.match(text)
        if ask:
            wanted = ask.group(1).strip().lower()
            project = next((p for p in await self._projects() if p["name"].lower() == wanted), None) or await self.find_in_text(ask.group(1))
            if project is None:
                return msg("proj_not_found", lang), []
            return await self.request_price(buyer, project, lang), []
        project = await self.find_in_text(text)
        if project is None:
            return None
        buttons = [ask_button(project)] if project["status"] != "SOLD_OUT" else []
        return card_text(project, lang) + ("\n\n" + msg("proj_ask_hint", lang) if buttons else ""), buttons

    async def handle_link(self, buyer: dict, payload: str, lang: str = "en") -> tuple[str, list[list[dict]]] | None:
        link = PROJECT_LINK.match(payload or "")
        if link is None:
            return None
        found = await self.repo.find_project_by_code(link.group(2))
        project = await self.repo.get_project(found["id"]) if found else None
        if project is None:
            return msg("proj_not_found", lang), []
        if link.group(1) == "proj":
            return await self.request_price(buyer, project, lang), []
        return card_text(project, lang) + "\n\n" + msg("proj_ask_hint", lang), [ask_button(project)]

    async def request_price(self, buyer: dict, project: dict, lang: str = "en") -> str:
        if project.get("price_from_etb") is not None:
            return msg("proj_price_known", lang, project=project["name"], developer=project["developer_name"],
                       price=fmt_etb(float(project["price_from_etb"])), note=(f" ({project['price_note']})" if project.get("price_note") else ""))
        row, is_new = await self.repo.create_price_request(project["id"], buyer["id"])
        if not is_new:
            return msg("proj_price_again", lang, project=project["name"], developer=project["developer_name"])
        who = buyer.get("full_name") or "A buyer"
        if self.proactive is not None:
            await self.proactive._notify_broker(
                "PRICE_REQUEST", f"pricereq:{row['id']}",
                f"{who} wants the price of {project['name']} ({project['developer_name']}, {project.get('sub_city') or 'Addis Ababa'}). "
                "Ask the developer, then enter the price on the Projects page and the buyer is told automatically.", buyer_id=buyer["id"])
        if self.settings.project_price_request_email and project.get("sales_email"):
            try:  # no buyer details are sent: this only asks the developer for the price list
                await self.services.email.send(
                    to=project["sales_email"], subject=f"Price list request: {project['name']}",
                    html=f"<p>Hello, a buyer represented by {self.settings.broker_name} is interested in {project['name']}. "
                         "Could you please share the current price list and payment plan? Thank you.</p>")
            except Exception:
                log.exception("price request email to the developer failed")
        return msg("proj_price_asked", lang, project=project["name"], developer=project["developer_name"])

    async def suggest_for(self, prefs: dict, lang: str = "en", limit: int = 2) -> tuple[str, list[list[dict]]]:
        return await suggest_projects(self.repo, prefs, lang, limit)

    async def _language_of(self, buyer: dict) -> str:
        """The language the buyer last wrote in, so a price notice reaches them in it."""
        from app.language import detect_language

        try:
            said = [m["content"] for m in await self.repo.list_messages(buyer["id"], limit=50) if m["role"] == "buyer" and m.get("content")]
        except Exception:
            return "en"
        return detect_language(said[-1]) if said else "en"

    # ---------------- the broker sets a price ----------------

    async def set_price(self, project_id: UUID | str, price_from_etb: float | None, note: str | None = None) -> dict:
        if price_from_etb is not None and not (0 < price_from_etb < 1e12):
            raise ProjectError("The price must be a positive amount in birr.")
        updated = await self.repo.set_project_price(project_id, price_from_etb, (note or "").strip() or None)
        if updated is None:
            raise LookupError("project not found")
        project = await self.repo.get_project(project_id)
        assert project is not None
        told = skipped = 0
        if price_from_etb is not None and self.proactive is not None:
            for req in await self.repo.open_price_requests(project_id):
                buyer = await self.repo.get_buyer(req["buyer_id"])
                if buyer is None:
                    continue
                text = msg("proj_price_known", await self._language_of(buyer), project=project["name"],
                           developer=project["developer_name"], price=fmt_etb(price_from_etb), note=(f" ({project['price_note']})" if project.get("price_note") else ""))
                if buyer["human_takeover"]:  # the broker owns this chat: tell the broker, not the buyer
                    await self.proactive._notify_broker("PROJECT_PRICE", f"projprice-broker:{project_id}:{buyer['id']}:{price_from_etb:g}",
                                                        f"{project['name']} now has a price ({fmt_etb(price_from_etb)}). {buyer.get('full_name') or 'A buyer'} asked for it in a chat you hold.",
                                                        buyer_id=buyer["id"])
                    skipped += 1
                    continue
                claim = await self.repo.claim_event(kind="PROJECT_PRICE", audience="buyer", dedupe_key=f"projprice:{project_id}:{buyer['id']}:{price_from_etb:g}", buyer_id=buyer["id"])
                if claim is None:
                    skipped += 1
                    continue
                channel = await self.proactive._deliver_to_buyer(buyer, text)
                if channel is None:
                    skipped += 1  # the claim stays pending and is retried the next time the price is saved
                    continue
                await self.proactive._remember(buyer, text)
                await self.proactive._finish(claim, text, channel)
                await self.repo.answer_price_request(req["id"])
                told += 1
        return {"project": project["name"], "price_from_etb": price_from_etb, "buyers_told": told, "skipped": skipped}

    # ---------------- advertising offers ----------------

    def ad_text(self, p: dict) -> str:
        lines = facts(p)
        if p.get("summary"):
            lines += ["", p["summary"]]
        lines += ["", "Interested? Tap Ask the price and we will get it for you."]
        return "\n".join(lines)

    async def _keyboard(self, p: dict) -> dict:
        username = await self.ads.bot_username()
        link = lambda action: f"https://t.me/{username}?start={action}_{project_code(p['id'])}"  # noqa: E731
        return {"inline_keyboard": [[{"text": PROJECT_BUTTONS[0][1], "url": link("proj")}, {"text": PROJECT_BUTTONS[1][1], "url": link("pinfo")}]]}

    async def publish_ad(self, project_id: UUID | str, *, dry_run: bool = True, kind: str = "MANUAL") -> dict:
        p = await self.repo.get_project(project_id)
        if p is None:
            raise LookupError("project not found")
        if p["status"] == "SOLD_OUT":
            raise ProjectError("That project is sold out, so it will not be advertised.")
        channel = self.settings.telegram_channel_id
        if not channel:
            raise ProjectError("TELEGRAM_CHANNEL_ID is not set, so there is nowhere to post.")
        text, markup = self.ad_text(p), await self._keyboard(p)
        if dry_run:
            return {"posted": False, "preview": text, "buttons": [b["text"] for row in markup["inline_keyboard"] for b in row], "photo_url": p.get("image_url")}
        key = f"{p.get('promo_text') or ''}|{p.get('promo_until') or ''}" if kind == "PROMO" else f"manual:{date.today()}"
        claim = await self.repo.claim_project_post(project_id=p["id"], kind=kind, promo_key=key, chat_id=channel, text=text)
        if claim is None:
            return {"posted": False, "reason": "This offer was already announced."}
        try:
            message_id = None
            if p.get("image_url"):
                try:
                    message_id = await self.services.telegram.send_photo(channel, p["image_url"], text, reply_markup=markup)
                except Exception:
                    try:
                        data, _ = await self.fetcher.get_bytes(p["image_url"])
                        message_id = await self.services.telegram.send_photo_bytes(channel, data, text, reply_markup=markup)
                    except Exception:
                        log.warning("the project picture could not be posted; posting the text only")
            if message_id is None:
                message_id = await self.services.telegram.send_message(channel, text, reply_markup=markup)
        except Exception as exc:
            await self.repo.release_project_post(claim["id"])
            log.exception("project ad failed")
            raise ProjectError(f"Telegram refused the post ({type(exc).__name__}). Is the bot an admin of the channel?") from None
        await self.repo.set_project_post_message(claim["id"], message_id)
        if self.events:
            self.events.publish("ad", property_id=str(p["id"]), what=kind, title=p["name"])
        return {"posted": True, "message_id": message_id, "text": text}

    async def run_promotion_ads(self, dry_run: bool = False) -> dict:
        out: dict[str, Any] = {"job": "promotion_ads", "dry_run": dry_run, "sent": 0, "skipped": 0, "failed": 0, "details": []}
        if not self.settings.telegram_channel_id:
            out["details"].append({"note": "No channel is set, so offers are not advertised."})
            return out
        for p in await self.repo.projects_with_live_promotion():
            try:
                result = await self.publish_ad(p["id"], dry_run=dry_run, kind="PROMO")
            except (ProjectError, LookupError) as exc:
                out["failed"] += 1
                out["details"].append({"project": p["name"], "error": str(exc)})
                continue
            if dry_run:
                out["details"].append({"project": p["name"], "would_post": p["promo_text"]})
            elif result.get("posted"):
                out["sent"] += 1
            else:
                out["skipped"] += 1
        return out
