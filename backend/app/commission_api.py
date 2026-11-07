"""The commission ledger beyond the deal board: the accounting export (and, in later slices, claims, assignment and documents)."""
from __future__ import annotations

import csv
import html
import io
import logging
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import audit
from app.access import Principal, require_role
from app.finance import fmt_etb

log = logging.getLogger(__name__)

router = APIRouter()

agent_access = require_role("agent")

EXPORT_COLUMNS = [
    ("deal_id", "Deal id"), ("created_at", "Created"), ("buyer_name", "Buyer"), ("developer_name", "Developer"),
    ("property_title", "Unit"), ("status", "Stage"), ("property_price_etb", "Listed price (ETB)"),
    ("contract_value_etb", "Contract value (ETB)"), ("expected_commission_etb", "Expected commission (ETB)"),
    ("lead_registered_at", "Lead registered"), ("next_buyer_milestone_date", "Next payment due"),
]


def safe_cell(value: object) -> str:
    """One CSV cell. A text that starts like a formula is prefixed so a spreadsheet shows it instead of running it."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def render_csv(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([safe_cell(row.get(key)) for key, _ in columns])
    return out.getvalue()


async def export_rows(request: Request) -> list[dict]:
    rows = await request.app.state.repo.list_ledger()
    return [{**r, "deal_id": str(r["id"])} for r in rows]


@router.get("/api/commission/export.csv")
async def export_commission(request: Request, p: Principal = Depends(agent_access)):
    body = render_csv(await export_rows(request), EXPORT_COLUMNS)
    name = f"commission-{date.today().isoformat()}.csv"
    return Response("﻿" + body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ---------------- claims to the developer (migration 035) ----------------

CLAIMABLE = ("CONTRACT_SIGNED", "COMMISSION_PENDING")


class ClaimIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ClaimResponseIn(BaseModel):
    status: Literal["ACKNOWLEDGED", "DISPUTED"]
    note: str | None = Field(default=None, max_length=500)


def email_is_live(settings) -> bool:
    return settings.email_backend == "resend" or (settings.email_backend == "auto" and bool(settings.resend_api_key))


def claim_html(*, agency: str, row: dict, note: str | None, contact: str | None) -> str:
    e = html.escape
    lines = [
        f"<p>{e(agency)} is claiming the agency commission on a sale it introduced to {e(row['developer_name'])}.</p>",
        "<ul>",
        f"<li>Buyer: <b>{e(row['buyer_name'] or 'the registered buyer')}</b></li>",
        f"<li>Unit: {e(row['property_title'])}</li>",
    ]
    if row.get("lead_registered_at"):
        lines.append(f"<li>Lead registered: {row['lead_registered_at']:%d %B %Y}</li>")
    if row.get("contract_value_etb") is not None:
        lines.append(f"<li>Contract value: {e(fmt_etb(float(row['contract_value_etb'])))}</li>")
    if row.get("expected_commission_etb") is not None:
        lines.append(f"<li>Commission claimed: {e(fmt_etb(float(row['expected_commission_etb'])))}</li>")
    lines.append(f"<li>Reference: {e(str(row['id']))}</li></ul>")
    if note:
        lines.append(f"<p>{e(note)}</p>")
    lines.append("<p>Please confirm receipt of this claim and the date the commission will be paid, or tell us what you dispute.</p>")
    if contact:
        lines.append(f"<p>Reply to this email to reach {e(contact)}.</p>")
    return "".join(lines)


async def _row(request: Request, ledger_id: UUID) -> dict:
    row = await request.app.state.repo.ledger_for_claim(ledger_id)
    if row is None:
        raise HTTPException(404, "Deal not found")
    return row


def _claim_view(row: dict, events: list[dict]) -> dict:
    return {
        "ledger_id": str(row["id"]), "claim_status": row["claim_status"], "claim_sent_at": row["claim_sent_at"], "claim_to": row["claim_to"],
        "claim_updated_at": row["claim_updated_at"], "overdue_flagged_at": row["overdue_flagged_at"],
        "events": [{"action": ev["action"], "note": ev["note"], "at": ev["created_at"]} for ev in events],
    }


@router.get("/api/ledger/{ledger_id}/claim")
async def get_claim(ledger_id: UUID, request: Request, p: Principal = Depends(require_role("viewer"))):
    row = await _row(request, ledger_id)
    return _claim_view(row, await request.app.state.repo.claim_events(ledger_id))


@router.post("/api/ledger/{ledger_id}/claim")
async def send_claim(ledger_id: UUID, body: ClaimIn, request: Request, p: Principal = Depends(agent_access)):
    """Email the developer's sales contact, from this agency's name, with the deal's facts. Nothing is recorded unless the email was sent."""
    app = request.app
    row = await _row(request, ledger_id)
    if row["status"] not in CLAIMABLE:
        raise HTTPException(409, "A claim can be sent once the contract is signed and until the commission is paid")
    if row["claim_status"] == "ACKNOWLEDGED":
        raise HTTPException(409, "The developer already acknowledged this claim")
    to = (row["sales_email"] or "").strip()
    if not to or to.endswith(".invalid"):
        raise HTTPException(422, f"{row['developer_name']} has no sales contact address on file")
    if not email_is_live(app.state.settings):
        raise HTTPException(503, "Email is not set up on this server, so no claim was sent. Nothing was recorded; send the claim by hand or set up email first.")
    profile = await app.state.repo.as_platform().org_profile(p.tenant.org_id)
    agency = (profile or {}).get("display_name") or (profile or {}).get("name") or "Your agency"
    contact = p.identity.email if p.identity else None
    note = (body.note or "").strip() or None
    sent_before = sum(1 for ev in await app.state.repo.claim_events(ledger_id) if ev["action"] == "claim_sent")
    try:
        message_id = await app.state.proactive.services.email.send(
            to=to, subject=f"Commission claim: {row['buyer_name'] or 'buyer'} - {row['property_title']}", html=claim_html(agency=agency, row=row, note=note, contact=contact),
            from_name=agency, reply_to=contact, idempotency_key=f"claim-{ledger_id}-{sent_before}")
    except Exception as exc:
        log.warning("commission claim email failed (%s)", type(exc).__name__)
        raise HTTPException(502, "The email could not be sent, so the claim was not recorded. Try again shortly.") from None
    updated = await app.state.repo.set_claim(ledger_id, "SENT", action="claim_sent", note=note, actor_id=p.tenant.user_id, to=to, email_id=message_id)
    await audit.record(request, p, "claim.send", row["developer_name"], deal=str(ledger_id), to=to)
    return _claim_view(updated, await app.state.repo.claim_events(ledger_id))


@router.put("/api/ledger/{ledger_id}/claim")
async def answer_claim(ledger_id: UUID, body: ClaimResponseIn, request: Request, p: Principal = Depends(agent_access)):
    """Record what the developer answered: acknowledged or disputed."""
    row = await _row(request, ledger_id)
    if row["claim_status"] is None:
        raise HTTPException(409, "No claim was sent for this deal yet")
    if row["claim_status"] == body.status:
        raise HTTPException(409, f"The claim is already marked {body.status.lower()}")
    note = (body.note or "").strip() or None
    action = "acknowledged" if body.status == "ACKNOWLEDGED" else "disputed"
    updated = await request.app.state.repo.set_claim(ledger_id, body.status, action=action, note=note, actor_id=p.tenant.user_id)
    await audit.record(request, p, "claim.acknowledge" if body.status == "ACKNOWLEDGED" else "claim.dispute", row["developer_name"], deal=str(ledger_id), note=note)
    return _claim_view(updated, await request.app.state.repo.claim_events(ledger_id))
