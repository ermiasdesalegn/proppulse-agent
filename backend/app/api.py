from __future__ import annotations

import hmac
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.chat import ChatService, Conflict, Inbound
from app.config import Settings
from app.events import sse_frames
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
async def daily_proactive_run(dry_run: bool = False, proactive=Depends(proactive_of)):
    return await proactive.run_all(dry_run)


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
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return {**await repo.stats(since), "high_value_threshold_etb": s.high_value_threshold_etb}


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
