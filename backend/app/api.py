from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.chat import ChatService, Conflict, Inbound
from app.config import Settings
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
