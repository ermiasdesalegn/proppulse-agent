from __future__ import annotations

from datetime import datetime
from html import escape


def _when(start: datetime) -> str:
    return start.strftime("%A, %d %B %Y at %H:%M")


def _etb(value: float) -> str:
    return f"{value:,.0f} ETB"


def lead_registration(*, broker_name: str, buyer: dict, developer: dict, properties: list[dict], start: datetime) -> tuple[str, str]:
    """Notice sent to a partner developer before the site visit to register the broker as the originating agent."""
    units = "".join(
        f"<li>{escape(p['project_name'])} - {escape(p['unit_type'])} ({escape(p['sub_city'])}), {_etb(float(p['price_etb']))}</li>"
        for p in properties
    )
    subject = f"Official Lead Registration Notice - {buyer.get('full_name') or 'Buyer'} - {properties[0]['project_name']}"
    html = (
        f"<p>Dear {escape(developer['name'])} Sales Team,</p>"
        f"<p>Under our brokerage agreement, I hereby register the following buyer as introduced by me, "
        f"<b>{escape(broker_name)}</b>, prior to their site visit.</p>"
        f"<ul><li><b>Buyer:</b> {escape(buyer.get('full_name') or '')}</li>"
        f"<li><b>Phone:</b> {escape(buyer.get('phone') or '')}</li>"
        f"<li><b>Site visit:</b> {escape(_when(start))}</li>"
        f"<li><b>Accompanying broker:</b> {escape(broker_name)}</li></ul>"
        f"<p>Units of interest:</p><ul>{units}</ul>"
        f"<p>Please record this registration against the date and time this email was sent.</p>"
    )
    return subject, html


def buyer_itinerary(*, broker_name: str, buyer: dict, stops: list[dict], start: datetime, distance_km: float, drive_minutes: int) -> tuple[str, str]:
    rows = "".join(
        f"<li>{i}. {escape(s['title'])} - {escape(s['sub_city'])}</li>" for i, s in enumerate(stops, start=1)
    )
    html = (
        f"<p>Hello {escape(buyer.get('full_name') or '')},</p>"
        f"<p>Your site visit is confirmed for <b>{escape(_when(start))}</b> with {escape(broker_name)}.</p>"
        f"<ol style='list-style:none;padding:0'>{rows}</ol>"
        + (f"<p>Total driving: about {distance_km:.1f} km, {drive_minutes} minutes between stops.</p>" if len(stops) > 1 and distance_km > 0 else "")
    )
    return "Your property tour is confirmed", html
