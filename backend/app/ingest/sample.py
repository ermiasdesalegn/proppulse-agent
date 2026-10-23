"""A mock developer listing page, for the Studio's 'load sample' button and for tests. No network involved.

Variant 1 lists Ayat's four units exactly as seeded, so reading it changes nothing.
Variant 2 is the same page a week later: one price cut, one unit gone, one new unit.
"""
from __future__ import annotations

from html import escape

from app.ingest.models import ScrapeProfile

PROFILE = ScrapeProfile(
    item="article.unit",
    fields={"project_name": ".project", "unit_type": ".type", "sub_city": ".area", "price": ".price", "sqm": ".size",
            "down_payment": ".down", "delivery": ".delivery", "description": ".desc"},
)

_BASE = [
    # project, type, area, price text, size, down payment, delivery, description
    ("Ayat Garden Residences", "3BR Townhouse", "Ayat", "ETB 14,500,000", "165 sqm", "30%", "June 2027", "Two-storey family townhouse with a private landscaped garden."),
    ("Ayat Heights", "2BR Apartment", "Ayat", "ETB 6,800,000", "88 sqm", "25%", "Dec 2026", "Compact two-bedroom apartment for first-time buyers."),
    ("Ayat Square Studios", "Studio", "Ayat", "ETB 3,600,000", "42 sqm", "20%", "Sep 2026", "Efficient studio close to public transport."),
    ("Ayat Hill Villas", "4BR Villa", "CMC", "ETB 28,000,000", "280 sqm", "35%", "Mar 2028", "Detached four-bedroom villa with a large garden and pool option."),
]


def sample_units(variant: int = 1) -> list[tuple]:
    units = [list(u) for u in _BASE]
    if variant == 2:
        units[1][3] = "ETB 6,200,000"                       # Ayat Heights: price cut
        units = [u for u in units if u[0] != "Ayat Square Studios"]  # sold out
        units.append(["Ayat Garden Residences", "2BR Townhouse", "Ayat", "ETB 9,900,000", "120 sqm", "30%", "Q4 2027", "Compact townhouse with a small garden."])
    return [tuple(u) for u in units]


def sample_listing(variant: int = 1) -> tuple[str, dict]:
    cards = "".join(
        f'<article class="unit"><h3 class="project">{escape(p)}</h3><p class="type">{escape(t)}</p><p class="area">{escape(a)}</p>'
        f'<p class="price">{escape(price)}</p><p class="size">{escape(size)}</p><p class="down">{escape(down)} down payment</p>'
        f'<p class="delivery">Delivery {escape(delivery)}</p><p class="desc">{escape(desc)}</p></article>'
        for p, t, a, price, size, down, delivery, desc in sample_units(variant)
    )
    html = f"<html><head><title>Ayat Real Estate - Available units</title></head><body><nav>Home | Projects | Contact</nav><main>{cards}</main><footer>Call 0911 000 000</footer></body></html>"
    return html, PROFILE.model_dump()
