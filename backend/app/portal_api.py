"""The buyer-facing website: public showcase endpoints and the signed-in buyer's own listings, saved homes and chat.

Nothing here takes the admin key. Public routes are open (and rate limited); /api/me routes need a Supabase access token, and every
query is keyed by the id inside that token, so a buyer can only ever read or change their own things.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from app.api import _clean_text, chat_of, repo_of
from app.buyer_auth import BuyerIdentity, current_buyer
from app.chat import ChatService, Inbound
from app.model3d import Layout, render_png
from app.repository import Repository

router = APIRouter()

MATCH_FIELDS = ("id", "title", "sub_city", "price_etb", "bedrooms", "similarity")  # what the agent recommended, without internal ids


def _unit(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the website receives it: the cover becomes one address and the lookup columns disappear."""
    out = {k: v for k, v in row.items() if k not in {"photo_url", "cover_project_id", "cover_media_id", "cover_image_url", "rn", "created_at"}}
    if row.get("photo_url"):  # the listing's own photo beats its project's picture
        out["cover"] = row["photo_url"]
    elif row.get("cover_media_id"):
        out["cover"] = f"/api/public/covers/{row['cover_project_id']}"
    else:
        out["cover"] = row.get("cover_image_url") or None
    return out


# ---------------- public ----------------

@router.get("/api/public/featured")
async def featured(response: Response, repo: Repository = Depends(repo_of)):
    facets = await repo.portal_facets()
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "units": [_unit(r) for r in await repo.portal_featured(6)],
        "stats": {"units": facets["units"], "developers": len(facets["developers"]), "areas": len(facets["sub_cities"])},
        "sub_cities": facets["sub_cities"][:8],
    }


@router.get("/api/public/covers/{project_id}")
async def cover(project_id: UUID, repo: Repository = Depends(repo_of)):
    row = await repo.portal_cover(project_id)
    if row is None:
        raise HTTPException(404, "Not found")
    return Response(bytes(row["data"]), media_type=row["mime"], headers={"Cache-Control": "public, max-age=3600"})


# ---------------- listings ----------------

@router.get("/api/me/listings")
async def listings(
    q: str | None = Query(default=None, max_length=80), sub_city: str | None = Query(default=None, max_length=80),
    min_bedrooms: int | None = Query(default=None, ge=0, le=20), price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0), developer: str | None = Query(default=None, max_length=60),
    sort: Literal["newest", "price_asc", "price_desc", "size_desc"] = "newest", limit: int = Query(default=24, ge=1, le=60), offset: int = Query(default=0, ge=0),
    me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of),
):
    rows, total = await repo.portal_units(q=q, sub_city=sub_city, min_bedrooms=min_bedrooms, price_min=price_min, price_max=price_max,
                                          developer=developer, sort=sort, limit=limit, offset=offset)
    return {"items": [_unit(r) for r in rows], "total": total, "facets": await repo.portal_facets(), "saved": await repo.saved_ids(me.user_id)}


@router.get("/api/me/listings/{property_id}")
async def listing(property_id: UUID, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    row = await repo.portal_unit(property_id)
    if row is None:
        raise HTTPException(404, "This home is not listed")
    return {**_unit(row), "saved": str(property_id) in await repo.saved_ids(me.user_id)}


@router.get("/api/me/listings/{property_id}/preview.png")
async def listing_preview(property_id: UUID, _: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    if await repo.portal_unit(property_id) is None:
        raise HTTPException(404, "This home is not listed")
    stored = await repo.get_layout(property_id)
    if stored is None:
        raise HTTPException(404, "This home has no 3D layout yet")
    return Response(await run_in_threadpool(render_png, Layout.model_validate(stored)), media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


# ---------------- saved homes ----------------

@router.get("/api/me/saved")
async def saved(me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    return {"items": [_unit(r) for r in await repo.portal_saved(me.user_id)]}


@router.put("/api/me/saved/{property_id}")
async def save_home(property_id: UUID, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    if not await repo.save_home(me.user_id, property_id):
        raise HTTPException(404, "This home is not listed")
    return {"saved": True}


@router.delete("/api/me/saved/{property_id}")
async def unsave_home(property_id: UUID, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    await repo.unsave_home(me.user_id, property_id)
    return {"saved": False}


# ---------------- chat with the agent ----------------

class MeChatIn(BaseModel):
    text: str = Field(max_length=2000)
    _strip = field_validator("text")(_clean_text)


async def _known_buyer(repo: Repository, me: BuyerIdentity) -> dict:
    """The buyer's CRM record, created on first contact and filled with the name and email from the account (never overwriting what the agent learned)."""
    buyer = await repo.get_or_create_buyer(me.thread_id)
    fill = {k: v for k, v in (("full_name", me.full_name), ("email", me.email)) if v and not buyer.get(k)}
    return await repo.update_buyer(buyer["id"], **fill) if fill else buyer


def _answer(outcome) -> dict:
    return {
        "reply": outcome.reply,
        "quick_replies": outcome.quick_replies,
        "takeover": outcome.takeover,
        "error": outcome.error,
        "active_matches": [{k: m[k] for k in MATCH_FIELDS if k in m} for m in outcome.state.get("active_matches", [])],
    }


@router.post("/api/me/chat")
async def chat(body: MeChatIn, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of), service: ChatService = Depends(chat_of)):
    await _known_buyer(repo, me)
    return _answer(await service.handle_inbound(Inbound(channel="web", external_id=me.user_id, kind="text", text=body.text)))


# "Ask the assistant about this home": the same deep links the channel ads use (ask / compare / book), so the agent opens exactly this unit.
OPEN_ACTIONS = {"ask": "Tell me about this home", "cmp": "Compare this home with similar ones", "book": "I would like to visit this home"}


class OpenHomeIn(BaseModel):
    property_id: UUID
    action: Literal["ask", "cmp", "book"] = "ask"


@router.post("/api/me/chat/open")
async def open_home(body: OpenHomeIn, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of), service: ChatService = Depends(chat_of)):
    unit = await repo.portal_unit(body.property_id)
    if unit is None:
        raise HTTPException(404, "This home is not listed")
    await _known_buyer(repo, me)
    inbound = Inbound(channel="web", external_id=me.user_id, kind="text", text="", start_payload=f"{body.action}_{body.property_id.hex[:8]}",
                      shown_as=f"{OPEN_ACTIONS[body.action]}: {unit['title']}"[:300])
    outcome = await service.handle_inbound(inbound)
    answer = _answer(outcome)
    if not answer["active_matches"] and not outcome.takeover and unit["status"] == "AVAILABLE":
        # The quick "ask" answer is written without the agent, so the opened home is named here for the page's side list.
        answer["active_matches"] = [{"id": str(unit["id"]), "title": unit["title"], "sub_city": unit["sub_city"], "price_etb": float(unit["price_etb"]),
                                     "bedrooms": unit["bedrooms"], "similarity": 1.0}]
    return answer


@router.get("/api/me/chat/messages")
async def chat_messages(after_id: int = Query(default=0, ge=0), me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    buyer = await repo.get_buyer_by_thread(me.thread_id)
    if buyer is None:
        return {"takeover": False, "messages": []}
    rows = await repo.list_messages(buyer["id"], after_id=after_id)
    keep = ("id", "role", "source", "content", "created_at")
    return {"takeover": bool(buyer["human_takeover"]), "messages": [{k: r[k] for k in keep if k in r} for r in rows]}
