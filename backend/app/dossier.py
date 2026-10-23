"""The broker's pre-tour closing dossier: everything needed to close the deal in person, on one card.

Built from the database at the moment it is asked for, so it is always current. The same data renders as chat text (Telegram), HTML
(email) and JSON (the dashboard).
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from typing import Any

from app import finance
from app.finance import fmt_etb, payment_schedule


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


async def build_dossier(repo: Any, tour_id: Any, *, today: date, timezone: str = "Africa/Addis_Ababa") -> dict:
    from zoneinfo import ZoneInfo

    tour = await repo.get_tour(tour_id)
    if tour is None:
        raise LookupError(f"tour {tour_id} not found")
    buyer = await repo.get_buyer(tour["buyer_id"])
    if buyer is None:
        raise LookupError(f"buyer {tour['buyer_id']} not found")
    stops = await repo.get_tour_stops(tour_id)
    props = await repo.get_properties([s["property_id"] for s in stops])
    said = [m["content"] for m in await repo.list_messages(buyer["id"], limit=200) if m["role"] == "buyer" and m["content"].strip()][-5:]

    units, total_value, total_commission = [], 0.0, 0.0
    for p in props:
        price = float(p["price_etb"])
        commission = price * float(p["commission_rate_pct"] or 0) / 100
        total_value += price
        total_commission += commission
        summary = finance.summarize(payment_schedule(p, today))
        units.append({
            "property_id": str(p["id"]), "title": p["title"], "developer": p["developer_name"], "area": p["sub_city"],
            "price_etb": price, "price_per_sqm_etb": round(price / float(p["sqm"])), "bedrooms": p["bedrooms"],
            "commission_rate_pct": _num(p["commission_rate_pct"]), "expected_commission_etb": round(commission),
            "payment_plan": finance.describe(summary, fmt_etb), "monthly_equivalent_etb": summary["monthly_equivalent_etb"],
            "reliability_score": _num(p.get("reliability_score")),
        })

    route = tour.get("mapbox_route_json") or {}
    start = tour["scheduled_start"]
    return {
        "tour_id": str(tour["id"]), "kind": tour.get("kind", "SITE_VISIT"),
        "starts_at": start.astimezone(ZoneInfo(timezone)).isoformat(),
        "buyer": {
            "id": str(buyer["id"]), "name": buyer.get("full_name"), "phone": buyer.get("phone"), "email": buyer.get("email"),
            "max_budget_etb": _num(buyer.get("max_budget_etb")), "min_bedrooms": buyer.get("min_bedrooms"),
            "areas": list(buyer.get("preferred_sub_cities") or []), "lead_score": buyer.get("lead_score"), "deal_stage": buyer.get("deal_stage"),
            "wants": buyer.get("preference_summary"),
        },
        "objections": [o["text"] for o in buyer.get("objections") or []],
        "recent_buyer_messages": said,
        "units": units,
        "route": {"distance_km": round(route["distance_meters"] / 1000, 1), "minutes": round(route["duration_seconds"] / 60)} if route.get("distance_meters") else None,
        "total_value_etb": round(total_value), "expected_commission_etb": round(total_commission),
        "budget_gap_etb": None if buyer.get("max_budget_etb") is None else round(total_value / max(len(units), 1) - float(buyer["max_budget_etb"])),
    }


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %d %b, %H:%M")


def render_text(d: dict) -> str:
    b = d["buyer"]
    kind = "Phone consultation" if d["kind"] == "CONSULTATION" else "Site visit"
    lines = [f"PRE-TOUR DOSSIER: {kind} {_when(d['starts_at'])}", "", f"Buyer: {b['name'] or 'unknown'} | {b['phone'] or 'no phone'}" + (f" | {b['email']}" if b["email"] else "")]
    wants = [fmt_etb(b["max_budget_etb"]) + " budget" if b["max_budget_etb"] else "", f"{b['min_bedrooms']}+ bedrooms" if b["min_bedrooms"] is not None else "",
             ", ".join(b["areas"])]
    lines.append("Wants: " + (" | ".join(x for x in wants if x) or "not stated yet") + f" | lead score {b['lead_score']}/100")
    if d["objections"]:
        lines += ["", "Worries raised:"] + [f"- {o}" for o in d["objections"]]
    if d["units"]:
        lines += ["", "Units:"]
        for i, u in enumerate(d["units"], start=1):
            lines.append(f"{i}. {u['title']} ({u['developer']}, {u['area']}): {fmt_etb(u['price_etb'])}, {u['payment_plan']}. Commission about {fmt_etb(u['expected_commission_etb'])} ({u['commission_rate_pct']:g}%)")
        if d["route"]:
            lines.append(f"Route: {d['route']['distance_km']} km, about {d['route']['minutes']} min driving")
        if d["budget_gap_etb"] is not None and d["budget_gap_etb"] > 0:
            lines.append(f"Heads-up: the average unit is {fmt_etb(d['budget_gap_etb'])} above their stated budget.")
        lines += ["", f"If they buy everything shown: {fmt_etb(d['total_value_etb'])} sold, about {fmt_etb(d['expected_commission_etb'])} commission."]
    if d["recent_buyer_messages"]:
        lines += ["", "Their latest words:"] + [f"- \"{m[:140]}\"" for m in d["recent_buyer_messages"][-3:]]
    return "\n".join(lines)


def render_html(d: dict) -> tuple[str, str]:
    b = d["buyer"]
    kind = "Phone consultation" if d["kind"] == "CONSULTATION" else "Site visit"
    rows = "".join(
        f"<tr><td>{escape(u['title'])}</td><td>{escape(u['developer'])}</td><td>{escape(fmt_etb(u['price_etb']))}</td>"
        f"<td>{escape(u['payment_plan'])}</td><td>{escape(fmt_etb(u['expected_commission_etb']))}</td></tr>" for u in d["units"]
    )
    table = (f"<table border='1' cellpadding='6' cellspacing='0'><tr><th>Unit</th><th>Developer</th><th>Price</th><th>Payment plan</th><th>Commission</th></tr>{rows}</table>"
             if rows else "")
    worries = "".join(f"<li>{escape(o)}</li>" for o in d["objections"]) or "<li>None raised yet</li>"
    html = (
        f"<h2>{escape(kind)} {escape(_when(d['starts_at']))}: {escape(b['name'] or 'buyer')}</h2>"
        f"<p><b>Phone:</b> {escape(b['phone'] or '-')} &middot; <b>Lead score:</b> {b['lead_score']}/100 &middot; <b>Stage:</b> {escape(str(b['deal_stage']))}</p>"
        f"<p><b>Budget:</b> {escape(fmt_etb(b['max_budget_etb'])) if b['max_budget_etb'] else 'not stated'}</p>"
        f"<h3>Worries raised</h3><ul>{worries}</ul>{('<h3>Units</h3>' + table) if table else ''}"
        + (f"<p><b>Route:</b> {d['route']['distance_km']} km, about {d['route']['minutes']} minutes of driving.</p>" if d["route"] else "")
        + f"<p><b>Expected commission if they buy everything shown:</b> {escape(fmt_etb(d['expected_commission_etb']))}</p>"
    )
    return f"Pre-tour dossier: {b['name'] or 'buyer'} on {_when(d['starts_at'])}", html


async def deliver_dossier(repo: Any, services: Any, settings: Any, tour_id: Any, *, today: date) -> dict:
    """Send the dossier to the broker (Telegram, and email where an address is configured) and note that it was sent.
    Nothing here may fail a booking: the caller treats any error as a missed notification."""
    import logging

    log = logging.getLogger(__name__)
    data = await build_dossier(repo, tour_id, today=today, timezone=settings.timezone)
    sent = []
    chat = settings.broker_telegram_chat_id or settings.demo_fallback_telegram_chat_id
    if chat:
        try:
            await services.telegram.send_message(chat, render_text(data))
            sent.append("telegram")
        except Exception:
            log.exception("dossier could not be sent on Telegram")
    if settings.demo_fallback_email:
        try:
            subject, html = render_html(data)
            await services.email.send(to=settings.demo_fallback_email, subject=subject, html=html, idempotency_key=f"dossier-{tour_id}")
            sent.append("email")
        except Exception:
            log.exception("dossier could not be emailed")
    if sent:
        await repo.mark_dossier_sent(tour_id)
    return {"tour_id": str(tour_id), "sent": sent}
