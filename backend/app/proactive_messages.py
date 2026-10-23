"""Wording for messages the system sends on its own. Plain templates: no model call, so they never fail,
never cost quota, and every number in them comes straight from the database."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.agent.nodes import fmt_etb


def _greeting(buyer: dict) -> str:
    name = (buyer.get("full_name") or "").split()
    return f"Hi {name[0]}," if name else "Hello,"


def _drop(item: dict) -> str:
    return f"{abs(float(item['change_pct'])):.1f}%"


def alert_text(buyer: dict, items: list[dict]) -> str:
    """items are PRICE_DROP / NEW_LISTING dicts, already in the order they will be numbered."""
    budget = buyer.get("max_budget_etb")
    if len(items) == 1:
        i = items[0]
        where = f"{i['title']} by {i['developer_name']} in {i['sub_city']}"
        if i["kind"] == "PRICE_DROP":
            fits = f", within the {fmt_etb(float(budget))} budget you mentioned" if budget else ""
            body = (f"good news: {where} just dropped from {fmt_etb(float(i['old_price_etb']))} to "
                    f"{fmt_etb(float(i['price_etb']))} ({_drop(i)} lower){fits}.")
        else:
            body = f"a new listing matches what you're looking for: {where}, {fmt_etb(float(i['price_etb']))}."
        return f"{_greeting(buyer)} {body} Want details, a comparison with similar options, or a site visit? Just reply."

    lines = []
    for n, i in enumerate(items, start=1):
        note = (f"down {_drop(i)} from {fmt_etb(float(i['old_price_etb']))}" if i["kind"] == "PRICE_DROP" else "new listing")
        lines.append(f"{n}. {i['title']} - {i['developer_name']}, {i['sub_city']}: {fmt_etb(float(i['price_etb']))} ({note})")
    return (f"{_greeting(buyer)} I have {len(items)} updates that match what you're looking for:\n\n" + "\n".join(lines)
            + "\n\nReply with a number to hear more, compare, or book a visit.")


def alert_email(buyer: dict, items: list[dict]) -> tuple[str, str]:
    first = items[0]
    subject = (f"Price drop: {first['title']}" if first["kind"] == "PRICE_DROP" and len(items) == 1
               else f"{len(items)} property updates for you" if len(items) > 1 else f"New listing: {first['title']}")
    body = "".join(f"<p>{escape(line)}</p>" for line in alert_text(buyer, items).split("\n") if line.strip())
    return subject, body


def tour_reminder_text(*, buyer: dict, start: datetime, now: datetime, stops: list[dict], broker: str, tz: str) -> str:
    local, today = start.astimezone(ZoneInfo(tz)), now.astimezone(ZoneInfo(tz)).date()
    day = "today" if local.date() == today else "tomorrow" if (local.date() - today).days == 1 else local.strftime("on %A")
    lines = [f"{_greeting(buyer)} a reminder that your site visit with {broker} is {day} at {local:%H:%M}.", "", "Stops:"]
    for n, s in enumerate(stops, start=1):
        link = f" https://www.google.com/maps/search/?api=1&query={s['lat']},{s['lng']}" if s.get("lat") is not None else ""
        lines.append(f"{n}. {s['title']}{link}")
    lines += ["", "Reply here if you need to change the time."]
    return "\n".join(lines)


def milestone_text(*, buyer: dict, row: dict, days: int) -> str:
    due = row["next_buyer_milestone_date"]
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    return (f"{_greeting(buyer)} a friendly reminder: your next payment milestone with {row['developer_name']} for "
            f"{row['property_title']} is due {when} ({due:%d %b %Y}). Paying on schedule keeps your agreement in good standing. "
            "Reply here if you have any questions.")


def overdue_notice(row: dict) -> str:
    return (f"Milestone overdue: {row.get('full_name') or 'the buyer'} was due to pay {row['developer_name']} for "
            f"{row['property_title']} on {row['next_buyer_milestone_date']:%d %b %Y}. Please check in.")


def commission_ready_notice(row: dict) -> str:
    amount = f" Expected commission: {fmt_etb(float(row['expected_commission_etb']))}." if row.get("expected_commission_etb") else ""
    return f"Commission ready to collect: {row['developer_name']} - {row['property_title']} ({row.get('full_name') or 'buyer'}).{amount}"


def takeover_match_notice(buyer: dict, item: dict) -> str:
    return (f"{buyer.get('full_name') or 'A buyer'} (you have this chat) matches {item['title']} in {item['sub_city']} at "
            f"{fmt_etb(float(item['price_etb']))}" + (f", down {_drop(item)}." if item["kind"] == "PRICE_DROP" else ", a new listing."))
