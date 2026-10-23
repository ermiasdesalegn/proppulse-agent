from __future__ import annotations

import base64
import binascii
import hmac
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.ads import AdError
from app.chat import ChatService, Conflict, Inbound
from app.events import sse_frames
from app.ingest.fetch import FetchError
from app.ingest.models import ScrapeProfile
from app.ingest.sample import sample_listing
from app.ingest.service import ExtractionError, IngestionService
from app.repository import Repository
from app.telegram_updates import TelegramUpdateHandler

router = APIRouter()


def _same(a: str | None, b: str) -> bool:
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


async def require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    key = request.app.state.settings.admin_api_key
    if not key:
        raise HTTPException(503, "ADMIN_API_KEY is not configured")
    if not _same(x_admin_key, key):
        raise HTTPException(401, "Invalid admin key")


async def require_cron_or_admin(
    request: Request, x_cron_secret: str | None = Header(default=None), x_admin_key: str | None = Header(default=None)
) -> None:
    """Cloud Scheduler authenticates with the cron secret; the dashboard's buttons use the admin key."""
    s = request.app.state.settings
    if not s.cron_secret and not s.admin_api_key:
        raise HTTPException(503, "Neither CRON_SECRET nor ADMIN_API_KEY is configured")
    if (s.cron_secret and _same(x_cron_secret, s.cron_secret)) or (s.admin_api_key and _same(x_admin_key, s.admin_api_key)):
        return
    raise HTTPException(401, "Invalid credentials")


def proactive_of(request: Request):
    return request.app.state.proactive


def projects_of(request: Request):
    return request.app.state.projects


def chat_of(request: Request) -> ChatService:
    return request.app.state.chat


def repo_of(request: Request) -> Repository:
    return request.app.state.repo


def _clean_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("text must not be empty")
    return value


class WebChatIn(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    text: str = Field(max_length=2000)
    _strip = field_validator("text")(_clean_text)


class BrokerReplyIn(BaseModel):
    buyer_id: UUID
    text: str = Field(max_length=4000)
    _strip = field_validator("text")(_clean_text)


class TakeoverIn(BaseModel):
    on: bool


class PriceIn(BaseModel):
    price_etb: float = Field(gt=0, lt=1e12)


def public_buyer(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "preference_embedding"}


# ---------------- Telegram ----------------

@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    secret = request.app.state.settings.telegram_webhook_secret
    if not secret or not _same(x_telegram_bot_api_secret_token, secret):
        raise HTTPException(403, "Forbidden")
    handler: TelegramUpdateHandler = request.app.state.telegram_handler
    handler.submit(await request.json())
    return {"ok": True}


# ---------------- web chat simulator ----------------

@router.post("/api/chat/web", dependencies=[Depends(require_admin)])
async def web_chat(body: WebChatIn, chat: ChatService = Depends(chat_of)):
    outcome = await chat.handle_inbound(Inbound(channel="web", external_id=body.session_id, kind="text", text=body.text))
    return {
        "buyer_id": outcome.buyer_id,
        "reply": outcome.reply,
        "quick_replies": outcome.quick_replies,
        "takeover": outcome.takeover,
        "error": outcome.error,
        "active_matches": outcome.state.get("active_matches", []),
    }


@router.get("/api/chat/web/{session_id}/messages", dependencies=[Depends(require_admin)])
async def web_chat_messages(session_id: str, after_id: int = 0, repo: Repository = Depends(repo_of)):
    if not (8 <= len(session_id) <= 64 and session_id.replace("-", "").replace("_", "").isalnum()):
        raise HTTPException(422, "Invalid session id")
    buyer = await repo.get_buyer_by_thread(f"web:{session_id}")
    return {"buyer_id": buyer and str(buyer["id"]), "takeover": bool(buyer and buyer["human_takeover"]),
            "messages": await repo.list_messages(buyer["id"], after_id=after_id) if buyer else []}


# ---------------- proactive jobs ----------------

@router.post("/api/cron/daily-proactive-run", dependencies=[Depends(require_cron_or_admin)])
async def daily_proactive_run(dry_run: bool = False, proactive=Depends(proactive_of), projects=Depends(projects_of)):
    result = await proactive.run_all(dry_run)
    result["jobs"].append(await projects.run_promotion_ads(dry_run))  # developers' current offers, advertised once each
    for key in ("sent", "skipped", "failed"):
        result[key] = sum(j[key] for j in result["jobs"])
    return result


@router.post("/api/cron/reverse-rag-alerts", dependencies=[Depends(require_cron_or_admin)])
async def reverse_rag_alerts(dry_run: bool = False, proactive=Depends(proactive_of)):
    return await proactive.reverse_rag_alerts(dry_run)


@router.post("/api/cron/tour-reminders", dependencies=[Depends(require_cron_or_admin)])
async def tour_reminders(dry_run: bool = False, proactive=Depends(proactive_of)):
    return await proactive.tour_reminders(dry_run)


@router.post("/api/cron/milestone-reminders", dependencies=[Depends(require_cron_or_admin)])
async def milestone_reminders(dry_run: bool = False, proactive=Depends(proactive_of)):
    return await proactive.milestone_reminders(dry_run)


@router.post("/api/cron/reminders", dependencies=[Depends(require_cron_or_admin)])
async def all_reminders(dry_run: bool = False, proactive=Depends(proactive_of)):
    """Tour and milestone reminders together: the dashboard's 'Send 24h Tour & Milestone Reminders' button."""
    jobs = [await proactive.tour_reminders(dry_run), await proactive.milestone_reminders(dry_run)]
    return {"dry_run": dry_run, "jobs": jobs, **{k: sum(j[k] for j in jobs) for k in ("sent", "skipped", "failed")}}


@router.get("/api/proactive/events", dependencies=[Depends(require_admin)])
async def proactive_events(audience: str | None = None, limit: int = 100, repo: Repository = Depends(repo_of)):
    if audience not in (None, "buyer", "broker"):
        raise HTTPException(422, "audience must be 'buyer' or 'broker'")
    return await repo.list_events(audience=audience, limit=min(max(limit, 1), 500))


@router.post("/api/properties/{property_id}/price", dependencies=[Depends(require_admin)])
async def change_price(property_id: UUID, body: PriceIn, repo: Repository = Depends(repo_of)):
    """Record a developer's price change. The next proactive run alerts buyers the unit now suits."""
    try:
        return await repo.update_property_price(property_id, body.price_etb)
    except LookupError:
        raise HTTPException(404, "Property not found") from None


# ---------------- market ingestion ----------------

def decode_image(b64: str, mime: str) -> tuple[bytes, str]:
    """Decode an uploaded image and make sure it is really what it claims to be (type by its first bytes) and not too large."""
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the image is not valid base64") from None
    if len(data) > 5_000_000:
        raise ValueError("the image is larger than 5 MB")
    signatures = {"image/png": (bytes.fromhex("89504e470d0a1a0a"),), "image/jpeg": (bytes.fromhex("ffd8ff"),), "image/webp": (b"RIFF",)}
    if mime not in signatures or not data.startswith(signatures[mime]) or (mime == "image/webp" and data[8:12] != b"WEBP"):
        raise ValueError("the file is not the image type it claims to be")
    return data, mime


class IngestIn(BaseModel):
    developer_slug: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    source: Literal["html", "text", "url"]
    html: str | None = Field(default=None, max_length=500_000)
    text: str | None = Field(default=None, max_length=20_000)
    url: str | None = Field(default=None, max_length=2048)
    profile: ScrapeProfile | None = None
    full_sync: bool = False
    dry_run: bool = False
    publish_ads: bool = False
    floor_plan_base64: str | None = Field(default=None, max_length=7_000_000)
    floor_plan_mime: Literal["image/png", "image/jpeg", "image/webp"] | None = None

    def floor_plan(self) -> tuple[bytes, str] | None:
        """The uploaded plan, checked: announcement text only, 5 MB at most, and the bytes must really be that image type."""
        if not self.floor_plan_base64:
            return None
        if self.source != "text" or not self.floor_plan_mime:
            raise ValueError("a floor plan can only be uploaded with announcement text, with its image type")
        return decode_image(self.floor_plan_base64, self.floor_plan_mime)


class ScrapeConfigIn(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    profile: ScrapeProfile | None = None
    fetch_mode: Literal["static", "browser", "auto"] | None = None  # auto: plain HTTP first, a browser only if no unit cards are found

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("the scrape address must start with http:// or https://")
        return value or None


def ingest_of(request: Request) -> IngestionService:
    return request.app.state.ingest


async def _announce(request: Request, report: dict) -> list[dict]:
    ads = getattr(request.app.state, "ads", None)
    return await ads.announce_report(report) if ads else []


class AttachmentIn(BaseModel):
    filename: str = Field(max_length=200)
    content_type: str = Field(max_length=100)
    base64: str = Field(max_length=7_000_000)


class AnnouncementIn(BaseModel):
    """An email from a developer, as a mail service's inbound webhook delivers it."""

    message_id: str = Field(min_length=1, max_length=300)
    sender: str = Field(min_length=3, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    text: str = Field(default="", max_length=20_000)
    html: str | None = Field(default=None, max_length=500_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=5)
    developer_slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,40}$")


@router.post("/api/webhooks/announcement")
async def announcement_webhook(
    body: AnnouncementIn, request: Request, x_webhook_secret: str | None = Header(default=None),
    service: IngestionService = Depends(ingest_of), repo: Repository = Depends(repo_of),
):
    """A developer's price announcement arrives by email. It is read and applied exactly like pasted text, once per message id."""
    secret = request.app.state.settings.ingest_webhook_secret
    if not secret or not _same(x_webhook_secret, secret):
        raise HTTPException(403, "Forbidden")
    if not await repo.claim_announcement(body.message_id, body.sender, body.subject):
        return {"status": "duplicate"}

    developer = await repo.get_developer_by_slug(body.developer_slug) if body.developer_slug else await repo.find_developer_for_sender(body.sender)
    if developer is None:
        await repo.finish_announcement(body.message_id, "IGNORED", None, {"reason": "the sender is not a known developer"})
        return {"status": "ignored", "reason": "unknown sender"}

    text = body.text.strip()
    if not text and body.html:
        from app.ingest.parse import page_text

        text = page_text(body.html, 20_000)
    text = f"{body.subject}\n\n{text}".strip() if body.subject else text
    plan = None
    for attachment in body.attachments:
        if attachment.content_type in ("image/png", "image/jpeg", "image/webp"):
            try:
                plan = decode_image(attachment.base64, attachment.content_type)
                break
            except ValueError:
                continue  # an unusable attachment must not stop the announcement itself
    try:
        if not text:
            raise ValueError("the email has no text to read")
        report = await service.run(developer_slug=developer["slug"], source="text", text=text, floor_plan=plan)
    except (ValueError, ExtractionError) as exc:
        await repo.finish_announcement(body.message_id, "FAILED", developer["id"], {"error": str(exc)})
        return {"status": "failed", "developer": developer["slug"], "error": str(exc)}
    except Exception:
        await repo.finish_announcement(body.message_id, "FAILED", developer["id"], {"error": "unexpected error"})
        raise
    ads = await _announce(request, report) if request.app.state.settings.ads_auto_publish else []
    await repo.finish_announcement(body.message_id, "PROCESSED", developer["id"], {"counts": report["counts"], "floor_plan_used": bool(report.get("floor_plan_for"))})
    return {"status": "processed", "developer": developer["slug"], "counts": report["counts"], "ads": ads}


@router.get("/api/announcements", dependencies=[Depends(require_admin)])
async def list_announcements(repo: Repository = Depends(repo_of)):
    return await repo.list_announcements()


@router.post("/api/ingest", dependencies=[Depends(require_admin)])
async def ingest(body: IngestIn, request: Request, service: IngestionService = Depends(ingest_of)):
    try:
        report = await service.run(developer_slug=body.developer_slug, source=body.source, html=body.html, text=body.text, url=body.url,
                                   profile=body.profile, full_sync=body.full_sync, dry_run=body.dry_run, floor_plan=body.floor_plan())
    except LookupError:
        raise HTTPException(404, "Developer not found") from None
    except (ValueError, FetchError) as exc:
        raise HTTPException(422, str(exc)) from None
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from None
    wants_ads = body.publish_ads or request.app.state.settings.ads_auto_publish
    report["ads"] = await _announce(request, report) if wants_ads and not body.dry_run else []
    return report


@router.get("/api/ingest/runs", dependencies=[Depends(require_admin)])
async def ingest_runs(limit: int = 20, repo: Repository = Depends(repo_of)):
    return await repo.list_ingest_runs(min(max(limit, 1), 100))


@router.get("/api/samples/listing", dependencies=[Depends(require_admin)])
async def sample_listing_page(variant: int = 1):
    if variant not in (1, 2):
        raise HTTPException(422, "variant must be 1 or 2")
    html, profile = sample_listing(variant)
    return {"developer_slug": "ayat", "html": html, "profile": profile, "variant": variant}


@router.get("/api/developers", dependencies=[Depends(require_admin)])
async def list_developers(repo: Repository = Depends(repo_of)):
    return await repo.list_developers()


async def _record_scrape_failure(request: Request, repo: Repository, dev: dict, error: str) -> None:
    """Keep a failed read in the run history and tell the broker once, when it has failed enough times in a row to be worth a message."""
    from app.ingest.health import judge

    await repo.record_ingest_run(developer_id=dev["id"], source="url", dry_run=False, full_sync=True,
                                 counts={"created": 0, "price_changed": 0, "removed": 0, "unchanged": 0, "skipped": 0}, report={"error": error}, error=error[:500])
    s = request.app.state.settings
    verdict = judge(await repo.recent_page_runs(dev["id"]), failures_before_alert=s.scrape_failures_before_alert)
    chat = s.broker_telegram_chat_id or s.demo_fallback_telegram_chat_id
    if chat and verdict["consecutive_failures"] == s.scrape_failures_before_alert:  # exactly once, on the run that crosses the line
        try:
            await request.app.state.chat.services.telegram.send_message(
                chat, f"Scraper alert: {dev['name']}'s page could not be read {verdict['consecutive_failures']} times in a row. Last error: {error[:200]}")
        except Exception:
            pass


@router.put("/api/developers/{slug}/scrape", dependencies=[Depends(require_admin)])
async def set_scrape_source(slug: str, body: ScrapeConfigIn, repo: Repository = Depends(repo_of)):
    if (body.url is None) != (body.profile is None):
        raise HTTPException(422, "give both the address and the selectors, or neither to clear them")
    try:
        return await repo.set_developer_scrape(slug, body.url, body.profile.model_dump() if body.profile else None, body.fetch_mode)
    except LookupError:
        raise HTTPException(404, "Developer not found") from None


class SuggestIn(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    html: str | None = Field(default=None, max_length=500_000)


@router.post("/api/developers/{slug}/suggest-profile", dependencies=[Depends(require_admin)])
async def suggest_scrape_profile(slug: str, body: SuggestIn, service: IngestionService = Depends(ingest_of), repo: Repository = Depends(repo_of)):
    """Let the language model propose selectors for a developer's page and test them. Nothing is saved: the broker reviews the preview."""
    from app.ingest.onboard import suggest_profile

    developer = await repo.get_developer_by_slug(slug)
    if developer is None:
        raise HTTPException(404, "Developer not found")
    if not (body.url or body.html):
        raise HTTPException(422, "give a page address or its HTML")
    try:
        html = body.html
        if html is None:
            fetcher = service.browser if developer.get("fetch_mode") == "browser" and service.browser else service.fetcher
            html = await fetcher.get_text(body.url)
        result = await suggest_profile(service.gateway, html, developer["name"], body.url or "")
        if not result["usable"] and body.html is None and service.browser is not None and developer.get("fetch_mode") != "static" and fetcher is not service.browser:
            try:  # the cards may be built by JavaScript: propose again from the rendered page
                retry = await suggest_profile(service.gateway, await service.browser.get_text(body.url), developer["name"], body.url or "")
                if retry["usable"]:
                    return {**retry, "rendered_in_browser": True}
            except FetchError:
                pass
        return result
    except FetchError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(502, "The language model could not propose selectors right now") from None


@router.get("/api/scrape/health", dependencies=[Depends(require_admin)])
async def scrape_health_report(request: Request, repo: Repository = Depends(repo_of)):
    from app.ingest.health import scrape_health

    return await scrape_health(repo, failures_before_alert=request.app.state.settings.scrape_failures_before_alert)


@router.get("/demo/js-listing", response_class=Response, include_in_schema=False)
async def demo_js_listing():
    """A page whose unit cards exist only after its script runs: a plain fetch sees none. For trying the browser renderer locally
    (the scraper must be allowed to reach this machine: SCRAPE_ALLOW_PRIVATE_HOSTS=true)."""
    import json as _json

    from app.ingest.sample import sample_units

    units = [dict(zip(("project", "type", "area", "price", "size", "down", "delivery", "desc"), u)) for u in sample_units(1)]
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>Ayat units</title></head><body><h1>Ayat Real Estate</h1><div id='list'>Loading...</div>"
            "<script>const units=" + _json.dumps(units) + ";document.getElementById('list').innerHTML=units.map(u=>`<article class=\"unit\">"
            "<h3 class=\"project\">${u.project}</h3><p class=\"type\">${u.type}</p><p class=\"area\">${u.area}</p><p class=\"price\">${u.price}</p>"
            "<p class=\"size\">${u.size}</p><p class=\"down\">${u.down} down payment</p><p class=\"delivery\">Delivery ${u.delivery}</p>"
            "<p class=\"desc\">${u.desc}</p></article>`).join('');</script></body></html>")
    return Response(page, media_type="text/html")


@router.post("/api/cron/scrape-market", dependencies=[Depends(require_cron_or_admin)])
async def scrape_market(request: Request, dry_run: bool = False, publish_ads: bool = False,
                        service: IngestionService = Depends(ingest_of), repo: Repository = Depends(repo_of)):
    """Read every developer that has a scrape address saved. One developer failing never stops the others."""
    details, totals = [], {"sent": 0, "skipped": 0, "failed": 0}
    configured = [d for d in await repo.list_developers() if d["scrape_url"] and d["scrape_profile"]]
    for dev in configured:
        try:
            report = await service.run(developer_slug=dev["slug"], source="url", full_sync=True, dry_run=dry_run)
        except (ValueError, FetchError, ExtractionError, LookupError) as exc:
            totals["failed"] += 1
            details.append({"developer": dev["name"], "error": str(exc)})
            if not dry_run:
                await _record_scrape_failure(request, repo, dev, str(exc))
            continue
        c = report["counts"]
        totals["sent"] += c["created"] + c["price_changed"] + c["removed"]
        totals["skipped"] += c["unchanged"]
        line = {"developer": dev["name"], "new": c["created"], "price changes": c["price_changed"], "removed": c["removed"], "unchanged": c["unchanged"]}
        if report["sync"].startswith("skipped"):
            line["warning"] = report["sync"]
        if (publish_ads or request.app.state.settings.ads_auto_publish) and not dry_run:
            line["ads posted"] = len([a for a in await _announce(request, report) if a.get("posted")])
        details.append(line)
    projects = getattr(request.app.state, "projects", None)
    started = await projects.start_market_sweep(dry_run) if projects is not None else {"started": False, "reason": ""}
    if started["started"]:  # the language-model sweep over the developers' own sites: no selectors, it runs in the background
        details.append({"note": f"Reading {started['sources']} developer sites in the background with the language model ({'preview' if dry_run else 'saving'}). "
                                "Follow it on the Projects page; it can take several minutes."})
    elif started.get("reason", "").startswith("A market sweep is already"):
        details.append({"note": "A market sweep is already running. See the Projects page."})
    elif not configured:
        details.append({"note": "No developer site is configured. Add developers with a website on the Projects page, or set MARKET_SOURCES."})
    job = {"job": "market_scraper", "dry_run": dry_run, **totals, "details": details}
    return {"dry_run": dry_run, **totals, "jobs": [job]}


# ---------------- channel ads ----------------

class AdPublishIn(BaseModel):
    property_id: UUID
    kind: Literal["MANUAL", "NEW_LISTING", "PRICE_DROP"] = "MANUAL"
    dry_run: bool = True  # previewing is the default: a post on the public channel cannot be taken back from here


@router.post("/api/ads/publish", dependencies=[Depends(require_admin)])
async def publish_ad(body: AdPublishIn, request: Request):
    ads = getattr(request.app.state, "ads", None)
    if ads is None:
        raise HTTPException(503, "Ads are not available")
    try:
        return await ads.publish(body.property_id, kind=body.kind, dry_run=body.dry_run)
    except LookupError:
        raise HTTPException(404, "Property not found") from None
    except AdError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/api/tours/{tour_id}/dossier", dependencies=[Depends(require_admin)])
async def tour_dossier(tour_id: UUID, request: Request, repo: Repository = Depends(repo_of)):
    from zoneinfo import ZoneInfo

    from app.dossier import build_dossier, render_text

    s = request.app.state.settings
    try:
        data = await build_dossier(repo, tour_id, today=datetime.now(ZoneInfo(s.timezone)).date(), timezone=s.timezone)
    except LookupError:
        raise HTTPException(404, "Tour not found") from None
    return {**data, "text": render_text(data)}


@router.post("/api/tours/{tour_id}/dossier/send", dependencies=[Depends(require_admin)])
async def send_tour_dossier(tour_id: UUID, request: Request, repo: Repository = Depends(repo_of)):
    from zoneinfo import ZoneInfo

    from app.dossier import deliver_dossier

    s = request.app.state.settings
    try:
        return await deliver_dossier(repo, request.app.state.chat.services, s, tour_id, today=datetime.now(ZoneInfo(s.timezone)).date())
    except LookupError:
        raise HTTPException(404, "Tour not found") from None


@router.get("/api/ads", dependencies=[Depends(require_admin)])
async def list_ads(limit: int = 20, repo: Repository = Depends(repo_of)):
    return await repo.list_channel_posts(min(max(limit, 1), 100))


# ---------------- live activity stream ----------------

@router.get("/api/stream/events", dependencies=[Depends(require_admin)])
async def stream_events(request: Request, last_event_id: str | None = Header(default=None), after: int = 0):
    """Server-Sent Events: every agent node transition, message, takeover and proactive send, live.
    Reconnecting clients send Last-Event-ID and receive what they missed."""
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    frames = sse_frames(request.app.state.events, after_id=resume, is_disconnected=request.is_disconnected)
    return StreamingResponse(frames, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ---------------- dashboard data ----------------

class DealMoveIn(BaseModel):
    status: str
    contract_value_etb: float | None = Field(default=None, gt=0, lt=1e13)
    next_buyer_milestone_date: date | None = None


@router.get("/api/board", dependencies=[Depends(require_admin)])
async def deal_board(repo: Repository = Depends(repo_of)):
    return await repo.board()


@router.patch("/api/ledger/{ledger_id}", dependencies=[Depends(require_admin)])
async def move_deal(ledger_id: UUID, body: DealMoveIn, repo: Repository = Depends(repo_of)):
    try:
        return await repo.move_deal(ledger_id, body.status, contract_value_etb=body.contract_value_etb,
                                    next_buyer_milestone_date=body.next_buyer_milestone_date)
    except LookupError:
        raise HTTPException(404, "Deal not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/api/properties", dependencies=[Depends(require_admin)])
async def list_properties(repo: Repository = Depends(repo_of)):
    return await repo.list_properties_for_map()


@router.get("/api/tours", dependencies=[Depends(require_admin)])
async def list_tours(limit: int = 100, repo: Repository = Depends(repo_of)):
    return await repo.list_tours(min(max(limit, 1), 500))


@router.get("/api/stats", dependencies=[Depends(require_admin)])
async def stats(request: Request, repo: Repository = Depends(repo_of)):
    s = request.app.state.settings
    since = datetime.now(UTC) - timedelta(hours=24)
    return {**await repo.stats(since), "trends": await repo.trends(30), "high_value_threshold_etb": s.high_value_threshold_etb, "demo_reset_enabled": s.demo_reset_enabled}


@router.get("/api/analytics", dependencies=[Depends(require_admin)])
async def analytics(days: int = 30, repo: Repository = Depends(repo_of)):
    from app.analytics import build

    return await build(repo, days=min(max(days, 1), 365))


class DemoResetIn(BaseModel):
    confirm: str


@router.post("/api/demo/reset", dependencies=[Depends(require_admin)])
async def demo_reset(body: DemoResetIn, request: Request, repo: Repository = Depends(repo_of)):
    """Wipe conversations, deals, tours and event history and restore the demo catalogue. Off unless DEMO_RESET_ENABLED is set."""
    from app.demo import reset_demo

    if not request.app.state.settings.demo_reset_enabled:
        raise HTTPException(403, "Resetting the demo is switched off (set DEMO_RESET_ENABLED=true to allow it)")
    if body.confirm != "RESET":
        raise HTTPException(422, 'Type RESET to confirm')
    report = await reset_demo(repo, request.app.state.chat.services.calendar, getattr(getattr(request.app.state, "projects", None), "embed", None))
    request.app.state.events.publish("demo_reset", deleted=sum(report["deleted"].values()))
    return report


@router.get("/api/messages/{message_id}/audio", dependencies=[Depends(require_admin)])
async def message_audio(message_id: int, request: Request, repo: Repository = Depends(repo_of)):
    """Stream a buyer's voice note so the broker can play it in the console."""
    row = await repo.get_message(message_id)
    if row is None or not row["telegram_file_id"]:
        raise HTTPException(404, "No audio for this message")
    try:
        audio = await request.app.state.chat.services.telegram.download_file(row["telegram_file_id"])
    except Exception:
        raise HTTPException(502, "The voice note could not be fetched from Telegram") from None
    return Response(audio, media_type="audio/ogg", headers={"Cache-Control": "private, max-age=3600"})


# ---------------- broker console ----------------

@router.get("/api/buyers", dependencies=[Depends(require_admin)])
async def list_buyers(limit: int = 100, repo: Repository = Depends(repo_of)):
    return await repo.list_buyers(min(max(limit, 1), 500))


@router.get("/api/buyers/{buyer_id}", dependencies=[Depends(require_admin)])
async def get_buyer(buyer_id: UUID, repo: Repository = Depends(repo_of)):
    buyer = await repo.get_buyer(buyer_id)
    if buyer is None:
        raise HTTPException(404, "Buyer not found")
    return public_buyer(buyer)


@router.get("/api/buyers/{buyer_id}/profile", dependencies=[Depends(require_admin)])
async def buyer_profile(buyer_id: UUID, repo: Repository = Depends(repo_of)):
    """Everything the CRM shows about one buyer: why the score is what it is, how it grew, what they worry about, what is booked."""
    from app.agent import scoring

    buyer = await repo.get_buyer(buyer_id)
    if buyer is None:
        raise HTTPException(404, "Buyer not found")
    earned = buyer.get("score_breakdown") or {}
    history = await repo.score_history(buyer_id)
    return {
        "buyer": public_buyer(buyer),
        "score": {"total": buyer["lead_score"], "components": scoring.components(earned), "explained": scoring.total(earned),
                  "history": [{"score": h["score"], "gained": h["gained"], "at": h["created_at"]} for h in history]},
        "objections": buyer.get("objections") or [],
        "tours": [t for t in await repo.list_tours(200) if str(t["buyer_id"]) == str(buyer_id)],
    }


@router.get("/api/buyers/{buyer_id}/messages", dependencies=[Depends(require_admin)])
async def buyer_messages(buyer_id: UUID, after_id: int = 0, repo: Repository = Depends(repo_of)):
    if await repo.get_buyer(buyer_id) is None:
        raise HTTPException(404, "Buyer not found")
    return await repo.list_messages(buyer_id, after_id=after_id)


@router.post("/api/buyers/{buyer_id}/takeover", dependencies=[Depends(require_admin)])
async def set_takeover(buyer_id: UUID, body: TakeoverIn, chat: ChatService = Depends(chat_of)):
    try:
        return public_buyer(await chat.set_takeover(str(buyer_id), body.on))
    except LookupError:
        raise HTTPException(404, "Buyer not found") from None


@router.post("/api/chat/broker-reply", dependencies=[Depends(require_admin)])
async def broker_reply(body: BrokerReplyIn, chat: ChatService = Depends(chat_of)):
    try:
        return await chat.broker_reply(str(body.buyer_id), body.text)
    except LookupError:
        raise HTTPException(404, "Buyer not found") from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(502, "The message could not be delivered to the buyer") from None


# ---------------- projects: developers that publish no unit prices ----------------

class CrawlIn(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    dry_run: bool = True


class ProjectPriceIn(BaseModel):
    price_from_etb: float | None = Field(default=None, gt=0, lt=1e12)
    note: str | None = Field(default=None, max_length=200)


class ProjectPromoIn(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    discount_pct: float | None = Field(default=None, gt=0, le=100)
    until: date | None = None


class ProjectAdIn(BaseModel):
    dry_run: bool = True


def _project_error(exc: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc.args[0] if exc.args else exc))


@router.get("/api/projects", dependencies=[Depends(require_admin)])
async def list_projects(repo: Repository = Depends(repo_of)):
    return await repo.list_projects()


@router.get("/api/price-requests", dependencies=[Depends(require_admin)])
async def list_price_requests(repo: Repository = Depends(repo_of)):
    return await repo.list_price_requests()


@router.post("/api/developers/{slug}/crawl-projects", dependencies=[Depends(require_admin)])
async def crawl_projects(slug: str, body: CrawlIn, projects=Depends(projects_of)):
    from app.projects import ProjectError

    try:
        return await projects.crawl_developer(slug, body.url, dry_run=body.dry_run)
    except (ProjectError, LookupError) as exc:
        raise _project_error(exc) from None


@router.put("/api/projects/{project_id}/price", dependencies=[Depends(require_admin)])
async def set_project_price(project_id: UUID, body: ProjectPriceIn, projects=Depends(projects_of)):
    from app.projects import ProjectError

    try:
        return await projects.set_price(project_id, body.price_from_etb, body.note)
    except (ProjectError, LookupError) as exc:
        raise _project_error(exc) from None


@router.put("/api/projects/{project_id}/promo", dependencies=[Depends(require_admin)])
async def set_project_promo(project_id: UUID, body: ProjectPromoIn, repo: Repository = Depends(repo_of)):
    row = await repo.set_project_promo(project_id, (body.text or "").strip() or None, body.discount_pct if body.text else None, body.until if body.text else None)
    if row is None:
        raise HTTPException(404, "project not found")
    return {"ok": True}


@router.post("/api/projects/{project_id}/ad", dependencies=[Depends(require_admin)])
async def project_ad(project_id: UUID, body: ProjectAdIn, projects=Depends(projects_of)):
    from app.projects import ProjectError

    try:
        return await projects.publish_ad(project_id, dry_run=body.dry_run)
    except (ProjectError, LookupError) as exc:
        raise _project_error(exc) from None


@router.post("/api/cron/promotion-ads", dependencies=[Depends(require_cron_or_admin)])
async def promotion_ads(dry_run: bool = False, projects=Depends(projects_of)):
    return await projects.run_promotion_ads(dry_run)


class DeveloperIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    sales_email: str = Field(min_length=5, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    website_url: str | None = Field(default=None, max_length=300, pattern=r"^https?://")
    commission_rate_pct: float = Field(default=3.0, ge=0, le=100)


@router.post("/api/developers", dependencies=[Depends(require_admin)])
async def create_developer(body: DeveloperIn, repo: Repository = Depends(repo_of)):
    import re as _re

    slug = _re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")
    row = await repo.create_developer(name=body.name.strip(), slug=slug, sales_email=body.sales_email.strip(), website_url=body.website_url, commission_rate_pct=body.commission_rate_pct)
    if row is None:
        raise HTTPException(409, "A developer with that name already exists.")
    return row


@router.get("/api/market-sweep", dependencies=[Depends(require_admin)])
async def market_sweep_status(projects=Depends(projects_of)):
    return {**projects.status(), "sources": [{"developer": slug, "url": url} for slug, url in await projects.sources()]}


class MediaIn(BaseModel):
    filename: str | None = Field(default=None, max_length=160)
    mime: str = Field(max_length=60)
    data_base64: str = Field(max_length=40_000_000)


class ImageUrlIn(BaseModel):
    url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")


@router.post("/api/projects/{project_id}/media", dependencies=[Depends(require_admin)])
async def add_project_media(project_id: UUID, body: MediaIn, projects=Depends(projects_of)):
    from app.projects import ProjectError

    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "The file could not be read.") from None
    try:
        row = await projects.add_media(project_id, filename=body.filename, mime=body.mime, data=data)
    except (ProjectError, LookupError) as exc:
        raise _project_error(exc) from None
    return row


@router.get("/api/projects/{project_id}/media", dependencies=[Depends(require_admin)])
async def list_project_media(project_id: UUID, repo: Repository = Depends(repo_of)):
    return await repo.list_project_media(project_id)


@router.get("/api/projects/{project_id}/media/{media_id}", dependencies=[Depends(require_admin)])
async def project_media_file(project_id: UUID, media_id: int, repo: Repository = Depends(repo_of)):
    row = await repo.get_project_media(media_id)
    if row is None or str(row["project_id"]) != str(project_id):
        raise HTTPException(404, "Not found")
    return Response(bytes(row["data"]), media_type=row["mime"], headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/api/projects/{project_id}/media/{media_id}", dependencies=[Depends(require_admin)])
async def delete_project_media(project_id: UUID, media_id: int, repo: Repository = Depends(repo_of)):
    row = await repo.get_project_media(media_id)
    if row is None or str(row["project_id"]) != str(project_id) or not await repo.delete_project_media(media_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.put("/api/projects/{project_id}/image", dependencies=[Depends(require_admin)])
async def set_project_image(project_id: UUID, body: ImageUrlIn, repo: Repository = Depends(repo_of)):
    if await repo.set_project_image(project_id, body.url) is None:
        raise HTTPException(404, "project not found")
    return {"ok": True}
