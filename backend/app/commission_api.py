"""The commission ledger beyond the deal board: the accounting export (and, in later slices, claims, assignment and documents)."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.access import Principal, require_role

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
