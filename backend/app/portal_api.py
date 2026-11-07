"""The buyer-facing website: public showcase endpoints and the signed-in buyer's own listings, saved homes and chat.

Nothing here takes the admin key. Public routes are open (and rate limited); /api/me routes need a Supabase access token, and every
query is keyed by the id inside that token, so a buyer can only ever read or change their own things.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from app.access import agency_scope
from app.agent.nodes import model_file_name
from app.api import _clean_text, chat_of, repo_of
from app.brochure import brochure_data
from app.brochure import render_pdf as brochure_pdf
from app.buyer_auth import BuyerIdentity, current_buyer
from app.chat import ChatService, Inbound
from app.ingest.portal import is_listing_photo
from app.model3d import Layout, render_png, to_glb
from app.repository import Repository
from app.tenancy import resolve_org_id

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(agency_scope)])

MATCH_FIELDS = ("id", "title", "sub_city", "price_etb", "bedrooms", "similarity")  # what the agent recommended, without internal ids


def _unit(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the website receives it: the cover becomes one address and the lookup columns disappear."""
    out = {k: v for k, v in row.items() if k not in {"photo_url", "cover_project_id", "cover_media_id", "cover_image_url", "rn", "created_at"}}
    if row.get("photo_url"):  # the listing's own photo beats its project's picture
        out["cover"] = row["photo_url"]
    elif row.get("cover_media_id"):
        out["cover"] = f"/api/public/covers/{row['cover_project_id']}"
    else:  # a developer's site often gives its logo as the project picture: a drawing is better than a logo
        image = row.get("cover_image_url")
        out["cover"] = image if image and is_listing_photo(image) else None
    return out


# Only what a listing card shows: the detail fields (and the source address, never sent to buyers) stay out of the public showcase.
_DETAIL_ONLY = {"description", "payment_plan", "vision_amenities", "source_url", "floor_plan_image_url"}


# ---------------- public ----------------

@router.get("/api/public/agency")
async def agency_card(response: Response, request: Request):
    """Who the visitor is talking to: the agency's name, colour and logo for the website's header. The agency came from the `X-Agency`
    header (no header: the original agency); an unknown or suspended one was already answered "not found"."""
    profile = await request.app.state.repo.as_platform().org_profile(resolve_org_id())
    if profile is None:
        raise HTTPException(404, "No such agency")
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"slug": profile["slug"], "name": profile["display_name"] or profile["name"], "brand_color": profile["brand_color"], "logo_url": profile["logo_url"]}


@router.get("/api/public/agencies")
async def agency_directory(response: Response, request: Request):
    """The agencies that chose to be listed. Nothing else about them: no counts, no members, no inactive ones."""
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"agencies": await request.app.state.repo.listed_agencies()}


@router.get("/api/public/featured")
async def featured(response: Response, hero: UUID | None = Query(None), repo: Repository = Depends(repo_of)):
    """`hero` names the listing whose photo leads the landing page (the site sets it); without it the page uses the first featured home."""
    facets = await repo.portal_facets()
    response.headers["Cache-Control"] = "public, max-age=60"
    chosen = await repo.portal_unit(hero) if hero else None
    return {
        "hero": {k: v for k, v in _unit(chosen).items() if k not in _DETAIL_ONLY} if chosen and chosen["status"] == "AVAILABLE" else None,
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


@router.get("/api/me/listings/{property_id}/model.glb")
async def listing_model(property_id: UUID, _: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    """The 3D model the broker built for this home, for the page's interactive viewer (and to download)."""
    unit = await repo.portal_unit(property_id)
    stored = await repo.get_layout(property_id) if unit else None
    if stored is None:
        raise HTTPException(404, "This home has no 3D layout yet")
    return Response(await run_in_threadpool(to_glb, Layout.model_validate(stored)), media_type="model/gltf-binary",
                    headers={"Cache-Control": "private, max-age=600", "Content-Disposition": f'inline; filename="{model_file_name(unit["title"])}"'})


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


def _files(message_id: int | None, attachments: list[dict] | None) -> list[dict]:
    """What the chat shows for a message's attachments: a picture to show inline, or a link to download. Each address names the message, so
    the file route can check the message is the buyer's own."""
    base, out = f"/api/me/chat/messages/{message_id}", []
    for a in attachments or []:
        if a.get("kind") == "model3d":
            out += [{"kind": "image", "url": f"{base}/model.png", "label": f"3D view: {a.get('title', '')}"},
                    {"kind": "download", "url": f"{base}/model.glb", "label": "Download 3D model (.glb)"}]
        elif a.get("kind") == "comparison":
            out.append({"kind": "download", "url": f"{base}/comparison.pdf", "label": "Download comparison (PDF)"})
        elif a.get("kind") == "location" and isinstance(a.get("lat"), int | float) and isinstance(a.get("lng"), int | float):
            out.append({"kind": "map", "lat": a["lat"], "lng": a["lng"], "label": f"Map: {a.get('title') or 'first stop'}"})
    return out


def _answer(outcome) -> dict:
    return {
        "reply": outcome.reply,
        "attachments": _files(outcome.message_id, outcome.attachments),
        "quick_replies": outcome.quick_replies,
        "takeover": outcome.takeover,
        "error": outcome.error,
        "active_matches": [{k: m[k] for k in MATCH_FIELDS if k in m} for m in outcome.state.get("active_matches", [])],
    }


@router.post("/api/me/chat")
async def chat(body: MeChatIn, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of), service: ChatService = Depends(chat_of)):
    await _known_buyer(repo, me)
    return _answer(await service.handle_inbound(Inbound(channel="web", external_id=me.user_id, kind="text", text=body.text)))


MAX_VOICE_BYTES = 3_000_000  # about 90 seconds of the 16 kHz mono WAV the website records
VOICE_TYPES = {"audio/wav", "audio/x-wav", "audio/ogg", "audio/mpeg", "audio/mp3", "audio/aac", "audio/flac", "audio/webm", "audio/mp4"}


@router.post("/api/me/chat/voice")
async def chat_voice(request: Request, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of),
                     service: ChatService = Depends(chat_of)):
    """A voice note from the website, sent as the raw audio body. It is transcribed the way a Telegram voice note is, and the buyer sees
    what was heard in the chat, so a mishearing can be corrected by typing."""
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime not in VOICE_TYPES:
        raise HTTPException(415, "Please send the voice note as audio")
    audio = await request.body()
    if not audio:
        raise HTTPException(422, "The recording was empty")
    if len(audio) > MAX_VOICE_BYTES:
        raise HTTPException(413, "Please keep voice notes under a minute and a half")
    await _known_buyer(repo, me)
    heard = await service.transcribe(audio, mime)
    if not heard:
        raise HTTPException(422, "We could not make out the recording. Please try again, or type your message.")
    inbound = Inbound(channel="web", external_id=me.user_id, kind="text", text=heard, shown_as=f"🎤 {heard}"[:2000])
    return {**_answer(await service.handle_inbound(inbound)), "heard": heard}


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
    return {"takeover": bool(buyer["human_takeover"]), "messages": [{**{k: r[k] for k in keep if k in r}, "attachments": _files(r["id"], r.get("attachments"))} for r in rows]}


# Files an agent message carries: made again from what is stored each time they are opened, never kept as files.
CHAT_FILES = {"model.png": "model3d", "model.glb": "model3d", "comparison.pdf": "comparison"}


@router.get("/api/me/chat/messages/{message_id}/{name}")
async def chat_file(request: Request, message_id: int, name: Literal["model.png", "model.glb", "comparison.pdf"],
                    me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    buyer = await repo.get_buyer_by_thread(me.thread_id)
    row = await repo.get_buyer_message(buyer["id"], message_id) if buyer else None  # only ever a message of this buyer's own conversation
    found = next((a for a in (row or {}).get("attachments") or [] if a.get("kind") == CHAT_FILES[name]), None)
    if row is None or found is None:
        raise HTTPException(404, "This file is not in your conversation")
    private = {"Cache-Control": "private, max-age=600"}
    if found["kind"] == "model3d":
        stored = await repo.get_layout(found["property_id"])
        if stored is None:
            raise HTTPException(404, "This home's 3D layout has been removed")
        layout = Layout.model_validate(stored)
        if name == "model.png":
            return Response(await run_in_threadpool(render_png, layout), media_type="image/png", headers=private)
        return Response(await run_in_threadpool(to_glb, layout), media_type="model/gltf-binary",
                        headers={**private, "Content-Disposition": f'attachment; filename="{model_file_name(found.get("title"))}"'})
    tour = await repo.get_tour(found["tour_id"])
    if tour is None or str(tour["buyer_id"]) != str(buyer["id"]):
        raise HTTPException(404, "This visit is no longer on record")
    stops = await repo.get_tour_stops(tour["id"])
    homes = await repo.get_properties([s["property_id"] for s in stops])
    if not homes:
        raise HTTPException(404, "The homes in this comparison are no longer listed")
    s = request.app.state.settings
    sheet = brochure_data(homes, today=row["created_at"].astimezone(ZoneInfo(s.timezone)).date(), broker_name=s.broker_name,
                          buyer_name=buyer.get("full_name"), route=tour.get("mapbox_route_json"))
    return Response(await run_in_threadpool(brochure_pdf, sheet), media_type="application/pdf",
                    headers={**private, "Content-Disposition": 'attachment; filename="property-comparison.pdf"'})


# ---------------- my visits and my alerts ----------------

def _visit(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """One booking as the buyer sees it. `status` is what the page shows; `can_cancel` is what the Cancel button is allowed to do."""
    cancelled = row["cancelled_at"] is not None
    past = row["scheduled_start"] <= now
    status = "cancelled" if cancelled else "past" if past else "upcoming"
    homes = list(row["homes"] or [])
    return {"id": str(row["id"]), "scheduled_start": row["scheduled_start"].isoformat(), "kind": row["kind"], "status": status,
            "can_cancel": status == "upcoming", "homes": homes, "project": row["project_name"]}


@router.get("/api/me/visits")
async def my_visits(me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    buyer = await repo.get_buyer_by_thread(me.thread_id)
    if buyer is None:
        return {"items": []}
    now = datetime.now(UTC)
    return {"items": [_visit(r, now) for r in await repo.buyer_tours(buyer["id"])]}


@router.post("/api/me/visits/{tour_id}/cancel")
async def cancel_visit(request: Request, tour_id: UUID, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of),
                       service: ChatService = Depends(chat_of)):
    """Cancel one of the signed-in buyer's own upcoming bookings: mark it, free the calendar slot, tell the broker. Someone else's booking
    answers exactly like one that does not exist."""
    buyer = await repo.get_buyer_by_thread(me.thread_id)
    tour = await repo.get_tour(tour_id)
    if buyer is None or tour is None or str(tour["buyer_id"]) != str(buyer["id"]):
        raise HTTPException(404, "We could not find that visit")
    if tour["cancelled_at"] is not None:
        raise HTTPException(409, "This visit is already cancelled")
    if tour["scheduled_start"] <= datetime.now(UTC):
        raise HTTPException(409, "This visit has already happened, so it cannot be cancelled")
    cancelled = await repo.cancel_buyer_tour(buyer["id"], tour_id)
    if cancelled is None:  # lost a race with another cancel, or the start time passed in between
        raise HTTPException(409, "This visit can no longer be cancelled")
    if tour.get("calendar_event_id"):
        try:
            await service.services.calendar.cancel(tour["calendar_event_id"])
        except Exception:  # the booking is cancelled either way; the broker is told below so the calendar can be fixed by hand
            log.exception("could not free the calendar slot of tour %s", tour_id)
    proactive = getattr(request.app.state, "proactive", None)
    if proactive is not None:
        s = request.app.state.settings
        when = tour["scheduled_start"].astimezone(ZoneInfo(s.timezone)).strftime("%a %d %b, %H:%M")
        who = buyer.get("full_name") or me.full_name or me.email or "A buyer"
        try:
            await proactive.notify_broker("PORTAL_ALERT", f"visit-cancelled:{tour_id}", f"{who} cancelled the visit on {when}. The calendar slot is free again.")
        except Exception:
            log.exception("could not tell the broker about the cancelled visit %s", tour_id)
    mine = next(r for r in await repo.buyer_tours(buyer["id"]) if r["id"] == tour["id"])
    return _visit(mine, datetime.now(UTC))


class AlertsIn(BaseModel):
    enabled: bool


@router.get("/api/me/alerts")
async def my_alerts(me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    buyer = await repo.get_buyer_by_thread(me.thread_id)
    return {"enabled": True if buyer is None else bool(buyer["alerts_enabled"])}


@router.put("/api/me/alerts")
async def set_my_alerts(body: AlertsIn, me: BuyerIdentity = Depends(current_buyer), repo: Repository = Depends(repo_of)):
    buyer = await _known_buyer(repo, me)  # a buyer who has never chatted gets their record now, so the choice is kept
    return {"enabled": bool((await repo.set_buyer_alerts(buyer["id"], body.enabled))["alerts_enabled"])}
