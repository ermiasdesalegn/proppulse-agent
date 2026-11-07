"""The platform earning money, as a DEMO (docs/PLAN_PRODUCT_AND_MONEY.md, phase 2; migration 038).

Nothing here moves real money. Two ways to earn are shown:
- a monthly plan: an owner upgrades on a mock checkout, the plan (and so the quotas of app/quota.py) changes at once and a receipt is kept;
- a share of the agency's PAID commission: a database trigger writes one `platform_fees` row when a deal reaches COMMISSION_PAID.

Prices and fee percentages are placeholders in the `plan_prices` table, not in code. `MockPaymentProvider` is the one place a payment is
decided; a real provider (Chapa, Telebirr) would replace it, and its webhook, refunds, lapse handling and monthly invoicing are the
"later" list in the plan document.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import audit
from app.access import Principal, require_role
from app.quota import PLAN_DAILY_JOBS, PLAN_DAILY_MESSAGES, PLAN_MONTHLY_BUDGET_USD, daily_jobs, daily_limit, monthly_budget

router = APIRouter()

BILLING_MODE = "demo"  # the only mode there is; every billing screen says so
DEMO_NOTICE = "DEMO MODE: no real money moves"

billing_admin = require_role("admin")  # looking at billing
billing_owner = require_role("owner")  # buying a plan


@dataclass(frozen=True)
class Payment:
    ok: bool
    reference: str


class MockPaymentProvider:
    """Stands in for a payment provider. It never contacts anyone and never touches money: it accepts every charge it is asked for.
    (The customer's choice to pay or cancel is made on the checkout screen; cancelling never reaches this class.)"""

    name = "mock"

    def charge(self, org_id: UUID, plan: str, amount_etb: float) -> Payment:
        return Payment(ok=True, reference=f"demo-{uuid4().hex[:12]}")


provider = MockPaymentProvider()


class UpgradeIn(BaseModel):
    plan: Literal["pro", "business"]


def _money(value: object) -> float:
    return float(value)  # type: ignore[arg-type]


async def overview(request: Request) -> dict:
    repo = request.app.state.repo
    prices = await repo.plan_prices()
    used = await repo.usage_today()
    plan = used["plan"]
    mine = next((p for p in prices if p["plan"] == plan), None)
    rank = mine["rank"] if mine else 0
    invoices = await repo.list_invoices()
    fees = await repo.list_platform_fees()
    return {
        "mode": BILLING_MODE, "demo_notice": DEMO_NOTICE, "plan": plan,
        "plans": [{
            "plan": p["plan"], "price_etb": _money(p["price_etb"]), "fee_pct": _money(p["fee_pct"]), "current": p["plan"] == plan,
            "can_upgrade": p["rank"] > rank, "daily_messages": PLAN_DAILY_MESSAGES[p["plan"]], "monthly_budget_usd": PLAN_MONTHLY_BUDGET_USD[p["plan"]],
            "daily_jobs": PLAN_DAILY_JOBS[p["plan"]],
        } for p in prices],
        "usage": {
            "buyer_messages_today": used["buyer_messages"], "daily_message_limit": daily_limit(plan),
            "jobs_today": await repo.jobs_today(), "daily_job_limit": daily_jobs(plan), "monthly_budget_usd": monthly_budget(plan),
        },
        "invoices": [{
            "id": str(i["id"]), "number": f"DEMO-{int(i['number']):05d}", "plan": i["plan"], "from_plan": i["from_plan"],
            "amount_etb": _money(i["amount_etb"]), "created_at": i["created_at"], "demo": True,
        } for i in invoices],
        "fees": [{
            "id": str(f["id"]), "ledger_id": str(f["ledger_id"]), "plan": f["plan"], "fee_pct": _money(f["fee_pct"]),
            "commission_etb": _money(f["commission_etb"]), "fee_etb": _money(f["fee_etb"]), "created_at": f["created_at"],
            "buyer_name": f.get("buyer_name"), "property_title": f.get("property_title"),
        } for f in fees],
        "fees_total_etb": round(sum(_money(f["fee_etb"]) for f in fees), 2),
        "invoices_total_etb": round(sum(_money(i["amount_etb"]) for i in invoices), 2),
    }


@router.get("/api/billing", dependencies=[Depends(billing_admin)])
async def get_billing(request: Request):
    return await overview(request)


@router.post("/api/billing/upgrade")
async def upgrade(request: Request, body: UpgradeIn, p: Principal = Depends(billing_owner)):
    """The mock checkout's "Pay (demo)" button. Only a higher plan can be bought; the price is read from the table, never from the caller."""
    repo = request.app.state.repo
    prices = {r["plan"]: r for r in await repo.plan_prices()}
    current = (await repo.usage_today())["plan"]
    if prices[body.plan]["rank"] <= prices[current]["rank"]:
        raise HTTPException(409, f"This agency is already on {current}, which is the same as or above {body.plan}")
    amount = _money(prices[body.plan]["price_etb"])
    payment = provider.charge(p.tenant.org_id, body.plan, amount)
    if not payment.ok:
        raise HTTPException(402, "The demo payment was declined")
    await repo.as_platform().buy_plan(p.tenant.org_id, from_plan=current, plan=body.plan, amount_etb=amount, created_by=p.tenant.user_id)
    await audit.record(request, p, "billing.upgrade", body.plan, old=current, new=body.plan, amount_etb=amount, reference=payment.reference, demo=True)
    return await overview(request)
