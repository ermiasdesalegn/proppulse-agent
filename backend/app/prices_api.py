"""An agency's own prices on top of the shared catalogue (migration 028).

The shared price stays the platform's (operator-only routes in app/api.py). Here an agency's agents set, change or clear the agency's
own price for a project or a unit. From then on its search, cards, brochures, payment schedules, dossiers and messages show that price,
and no other agency sees it. A project's waiting buyers are told in a queued job for this agency only. Each change is audited.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import audit
from app.access import Principal, console_access, require_role
from app.finance import fmt_etb

router = APIRouter()

agency_agent = require_role("agent")

Kind = Literal["project", "property"]


class AgencyPriceIn(BaseModel):
    price_etb: float = Field(gt=0, lt=1e12)
    note: str | None = Field(default=None, max_length=200)
    valid_until: date | None = None


@router.get("/api/agency-prices", dependencies=[Depends(console_access)])
async def list_agency_prices(request: Request):
    return {"prices": await request.app.state.repo.list_agency_prices()}


async def _target_name(request: Request, kind: str, target_id: UUID) -> str:
    repo = request.app.state.repo
    if kind == "project":
        row = await repo.get_project(target_id)
        name = row and row["name"]
    else:
        rows = await repo.get_properties([target_id])
        name = rows[0]["title"] if rows else None
    if not name:
        raise HTTPException(404, f"No such {kind}")
    return str(name)


@router.put("/api/agency-prices/{kind}/{target_id}")
async def set_agency_price(request: Request, kind: Kind, target_id: UUID, body: AgencyPriceIn, p: Principal = Depends(agency_agent)):
    if body.valid_until is not None and body.valid_until < date.today():
        raise HTTPException(422, "valid_until is in the past")
    name = await _target_name(request, kind, target_id)
    note = (body.note or "").strip() or None
    if kind == "project":
        result = await request.app.state.projects.set_agency_price(target_id, body.price_etb, note, body.valid_until, by=p.tenant.user_id)
    else:
        await request.app.state.repo.set_agency_price("property", target_id, body.price_etb, note=note, valid_until=body.valid_until, created_by=p.tenant.user_id)
        result = {"property": name, "agency_price_etb": body.price_etb}
    await audit.record(request, p, "price.override_set", f"{kind} {name}", price=fmt_etb(body.price_etb),
                       valid_until=body.valid_until.isoformat() if body.valid_until else None)
    return result


@router.delete("/api/agency-prices/{kind}/{target_id}")
async def clear_agency_price(request: Request, kind: Kind, target_id: UUID, p: Principal = Depends(agency_agent)):
    name = await _target_name(request, kind, target_id)
    if kind == "project":
        if (await request.app.state.repo.get_project(target_id))["agency_price_etb"] is None:
            raise HTTPException(404, "This agency has no price of its own for it")
        result = await request.app.state.projects.set_agency_price(target_id, None, by=p.tenant.user_id)
    else:
        if not await request.app.state.repo.clear_agency_price("property", target_id):
            raise HTTPException(404, "This agency has no price of its own for it")
        result = {"property": name, "agency_price_etb": None}
    await audit.record(request, p, "price.override_clear", f"{kind} {name}")
    return result
