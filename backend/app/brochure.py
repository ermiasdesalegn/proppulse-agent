"""A side-by-side brochure of the units a buyer is going to see, sent with the tour itinerary.

`brochure_data` is the single source of the numbers; `render_html` goes in the email body and `render_pdf` is attached. Everything shown
comes from the database and the payment-plan calculation, never from model-written text.
"""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from app import finance
from app.finance import fmt_etb, payment_schedule

ROWS = (
    ("developer", "Developer"), ("area", "Area"), ("size", "Size"), ("price", "Price"), ("per_sqm", "Price per sqm"),
    ("plan", "Payment plan"), ("monthly", "Average per month"), ("stage", "Construction"), ("delivery", "Delivery"),
    ("yield", "Estimated rental yield"), ("features", "Features"),
)


def brochure_data(props: list[dict], *, today: date, broker_name: str, buyer_name: str | None, route: dict | None = None) -> dict:
    units = []
    for p in props:
        price, sqm = float(p["price_etb"]), float(p["sqm"])
        summary = finance.summarize(payment_schedule(p, today))
        units.append({
            "title": p["title"],
            "cells": {
                "developer": p["developer_name"], "area": p["sub_city"], "size": f"{p['bedrooms']} bed, {sqm:g} sqm",
                "price": fmt_etb(price), "per_sqm": f"{price / sqm:,.0f} ETB",
                "plan": finance.describe(summary, fmt_etb), "monthly": fmt_etb(summary["monthly_equivalent_etb"]),
                "stage": "-" if p.get("construction_stage_pct") is None else f"{float(p['construction_stage_pct']):g}% built",
                "delivery": "-" if p.get("delivery_date") is None else p["delivery_date"].strftime("%B %Y"),
                "yield": "-" if p.get("est_rental_yield_pct") is None else f"{float(p['est_rental_yield_pct']):g}%",
                "features": ", ".join(((p.get("vision_amenities") or {}).get("tags") or [])[:5]) or "-",
            },
            "schedule": [pay.to_json() for pay in payment_schedule(p, today)],
        })
    return {"broker": broker_name, "buyer": buyer_name, "units": units, "route": route}


def render_html(data: dict) -> str:
    head = "".join(f"<th>{escape(u['title'])}</th>" for u in data["units"])
    body = "".join(
        f"<tr><td><b>{escape(label)}</b></td>" + "".join(f"<td>{escape(u['cells'][key])}</td>" for u in data["units"]) + "</tr>" for key, label in ROWS
    )
    route = ""
    if data.get("route") and data["route"].get("distance_meters"):
        route = f"<p>Your route: about {data['route']['distance_meters'] / 1000:.1f} km, roughly {data['route']['duration_seconds'] // 60} minutes of driving.</p>"
    return (
        f"<h2>Your property comparison</h2><p>Prepared for {escape(data['buyer'] or 'you')} by {escape(data['broker'])}.</p>{route}"
        f"<table border='1' cellpadding='6' cellspacing='0'><tr><th></th>{head}</tr>{body}</table>"
        "<p style='color:#666;font-size:12px'>Prices and payment terms are those advertised by the developers and may change. "
        "Payment dates assume signing a week from the date of this message.</p>"
    )


def _latin(text: str) -> str:
    """The built-in PDF fonts only know Latin-1; anything else becomes a plain stand-in rather than an error."""
    return text.replace("–", "-").replace("—", "-").replace("’", "'").encode("latin-1", "replace").decode("latin-1")


def render_pdf(data: dict) -> bytes | None:
    """The brochure as a PDF, or None if the PDF library is not installed (the email then carries the HTML only)."""
    try:
        from fpdf import FPDF
    except ImportError:
        return None
    units = data["units"]
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, _latin("Your property comparison"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _latin(f"Prepared for {data['buyer'] or 'you'} by {data['broker']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    label_w = 42
    col_w = (pdf.w - pdf.l_margin - pdf.r_margin - label_w) / max(len(units), 1)

    def row(label: str, cells: list[str], bold: bool = False, shade: bool = False) -> None:
        label, cells = _latin(label), [_latin(c) for c in cells]  # measuring text needs it in the font's alphabet too
        pdf.set_font("Helvetica", "B" if bold else "", 9)
        height = max(pdf.get_string_width(c) // max(col_w - 3, 1) + 1 for c in cells) * 5 + 2 if cells else 7
        if pdf.get_y() + height > pdf.h - 12:
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_fill_color(235, 238, 245) if shade else pdf.set_fill_color(255, 255, 255)
        pdf.rect(pdf.l_margin, y, label_w + col_w * len(cells), height, style="F" if shade else "")
        pdf.set_xy(pdf.l_margin, y)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(label_w, 5, _latin(label), border=0, new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "B" if bold else "", 9)
        for cell in cells:
            pdf.multi_cell(col_w, 5, _latin(cell), border=0, new_x="RIGHT", new_y="TOP")
        pdf.set_xy(pdf.l_margin, y + height)

    row("", [u["title"] for u in units], bold=True, shade=True)
    for i, (key, label) in enumerate(ROWS):
        row(label, [u["cells"][key] for u in units], shade=i % 2 == 1)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, _latin("Prices and payment terms are those advertised by the developers and may change. Payment dates assume signing a week from the date of this message."))
    return bytes(pdf.output())
