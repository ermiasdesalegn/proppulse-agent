"""The platform operator's view of every agency, and the controls for its plan and status (docs/PLAN_PRODUCT_AND_MONEY.md, phase 2).

Only the platform (the admin key) and admins of the operator's own agency may call these. Changing a plan changes what the agency's daily
message cap, monthly model budget and daily job cap are (app/quota.py). A suspended agency's members are refused, its bot's webhook is
refused, its public pages are "not found" and the scheduled jobs skip it (each of those checks already reads `status`). Every change is
written to the agency's own audit trail.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator

from app import audit
from app.access import OPERATOR_ORG_ID, Principal, market_admin

router = APIRouter()

Plan = Literal["free", "pro", "business"]
OrgStatus = Literal["active", "suspended"]


class AgencyChange(BaseModel):
    plan: Plan | None = None
    status: OrgStatus | None = None

    @model_validator(mode="after")
    def something(self) -> AgencyChange:
        if self.plan is None and self.status is None:
            raise ValueError("send a plan, a status, or both")
        return self


def _shown(row: dict) -> dict:
    return {
        "id": str(row["id"]), "slug": row["slug"], "name": row["name"], "plan": row["plan"], "status": row["status"],
        "created_at": row["created_at"], "operator": row["id"] == OPERATOR_ORG_ID,
        "members": int(row["members"]), "buyers": int(row["buyers"]), "messages_today": int(row["messages_today"]),
    }


@router.get("/api/operator/agencies")
async def list_agencies(request: Request, _: Principal = Depends(market_admin)):
    return {"agencies": [_shown(r) for r in await request.app.state.repo.as_platform().operator_agencies()]}


@router.patch("/api/operator/agencies/{org_id}")
async def change_agency(request: Request, org_id: UUID, body: AgencyChange, p: Principal = Depends(market_admin)):
    repo = request.app.state.repo.as_platform()
    before = await repo.organization(org_id)
    if before is None:
        raise HTTPException(404, "No such agency")
    if org_id == OPERATOR_ORG_ID and body.status == "suspended":
        raise HTTPException(409, "The operator's own agency cannot be suspended")
    after = await repo.set_organization_plan_status(org_id, plan=body.plan, status=body.status)
    if body.plan is not None and after["plan"] != before["plan"]:
        await audit.record(request, p, "org.plan_change", before["slug"], org_id=org_id, old=before["plan"], new=after["plan"])
    if body.status is not None and after["status"] != before["status"]:
        await audit.record(request, p, "org.status_change", before["slug"], org_id=org_id, old=before["status"], new=after["status"])
    rows = [r for r in await repo.operator_agencies() if r["id"] == org_id]
    return _shown(rows[0])
