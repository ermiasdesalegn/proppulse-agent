"""An agency's own commission terms with a developer (migration 034).

The developer's rate stays the shared default (platform only). Here an agency's admins record the rate they agreed with a developer. From
then on that agency's expected commissions, dossiers and analytics use it, and no other agency sees it. Each change is audited.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import audit
from app.access import Principal, console_access, require_role

router = APIRouter()

agency_admin = require_role("admin")


class DeveloperTermIn(BaseModel):
    rate_pct: float = Field(ge=0, le=100)
    note: str | None = Field(default=None, max_length=200)
    valid_until: date | None = None


def _shown(row: dict) -> dict:
    return {
        "developer_id": str(row["developer_id"]), "name": row["name"], "slug": row["slug"],
        "shared_rate_pct": float(row["shared_rate_pct"]),
        "agency_rate_pct": None if row["agency_rate_pct"] is None else float(row["agency_rate_pct"]),
        "effective_rate_pct": float(row["effective_rate_pct"]),
        "note": row["note"], "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
    }


async def _developer(request: Request, developer_id: UUID) -> dict:
    for row in await request.app.state.repo.list_developer_terms():
        if row["developer_id"] == developer_id:
            return row
    raise HTTPException(404, "No such developer")


@router.get("/api/developer-terms", dependencies=[Depends(console_access)])
async def list_developer_terms(request: Request):
    return {"developers": [_shown(r) for r in await request.app.state.repo.list_developer_terms()]}


@router.put("/api/developer-terms/{developer_id}")
async def set_developer_term(request: Request, developer_id: UUID, body: DeveloperTermIn, p: Principal = Depends(agency_admin)):
    if body.valid_until is not None and body.valid_until < date.today():
        raise HTTPException(422, "valid_until is in the past")
    dev = await _developer(request, developer_id)
    note = (body.note or "").strip() or None
    await request.app.state.repo.set_developer_term(developer_id, body.rate_pct, note=note, valid_until=body.valid_until, created_by=p.tenant.user_id)
    await audit.record(request, p, "terms.set", dev["name"], rate_pct=body.rate_pct, was=float(dev["effective_rate_pct"]),
                       valid_until=body.valid_until.isoformat() if body.valid_until else None)
    return _shown(await _developer(request, developer_id))


@router.delete("/api/developer-terms/{developer_id}")
async def clear_developer_term(request: Request, developer_id: UUID, p: Principal = Depends(agency_admin)):
    dev = await _developer(request, developer_id)
    if dev["agency_rate_pct"] is None:
        raise HTTPException(404, "This agency has no terms of its own with that developer")
    await request.app.state.repo.clear_developer_term(developer_id)
    await audit.record(request, p, "terms.clear", dev["name"], was=float(dev["agency_rate_pct"]))
    return _shown(await _developer(request, developer_id))
