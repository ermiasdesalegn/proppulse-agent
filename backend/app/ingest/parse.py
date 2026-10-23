"""Turning listing pages into units. Everything here is a pure function: no network, no model, no database."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import ValidationError

from app.geo import normalize_sub_city
from app.ingest.models import ListingUnit, ScrapeProfile

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_MULTIPLIERS = {"million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}

_MONEY = re.compile(r"(\d[\d,\s]*(?:\.\d+)?)\s*(million|mn|thousand|m|k)?\b", re.I)


def parse_money(text: str | None) -> float | None:
    """'14,500,000 ETB', 'ETB 14.5M', '14.5 million birr', 'Br. 6,800 k' -> birr. None if there is no amount."""
    if not text:
        return None
    match = _MONEY.search(text.replace(" ", " "))
    if not match:
        return None
    value = float(re.sub(r"[,\s]", "", match.group(1)))
    value *= _MULTIPLIERS.get((match.group(2) or "").lower(), 1.0)
    return value if value > 0 else None


def parse_area(text: str | None) -> float | None:
    """'165 sqm', '165 m2', '1,200 sq ft' -> square metres."""
    if not text:
        return None
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    if re.search(r"sq\.?\s*ft|square\s*f(?:ee|oo)t|ft2|ft²", text, re.I):
        value *= 0.092903
    return round(value, 2) if value > 0 else None


def parse_bedrooms(text: str | None) -> int | None:
    """'3BR', '3 bedrooms', 'Three bedroom', 'Studio' -> number of bedrooms (0 for a studio)."""
    if not text:
        return None
    lowered = text.lower()
    if "studio" in lowered:
        return 0
    if match := re.search(r"(\d+)\s*(?:br\b|bed|b/r|bdrm)", lowered):
        return int(match.group(1))
    if match := re.search(r"\b(" + "|".join(_NUMBER_WORDS) + r")[\s-]*(?:bed|br)", lowered):
        return _NUMBER_WORDS[match.group(1)]
    return int(text.strip()) if text.strip().isdigit() else None


def parse_percent(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match.group(1))
    return value if 0 <= value <= 100 else None


def parse_date(text: str | None) -> date | None:
    """'2027-06-30', '30 June 2027', 'June 2027', 'Q2 2027', '2027' -> a date (the end of the period for loose ones)."""
    if not text:
        return None
    s = text.strip().lower()
    if match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    if match := re.search(r"q([1-4])\D*(\d{4})", s):
        month = int(match.group(1)) * 3
        return date(int(match.group(2)), month, calendar.monthrange(int(match.group(2)), month)[1])
    if match := re.search(r"(?:(\d{1,2})\s+)?([a-z]{3})[a-z]*[\s,.\-/]+(\d{4})", s):
        month = _MONTHS.get(match.group(2))
        if month:
            year = int(match.group(3))
            day = int(match.group(1)) if match.group(1) else calendar.monthrange(year, month)[1]
            try:
                return date(year, month, day)
            except ValueError:
                return None
    if match := re.fullmatch(r"(\d{4})", s):
        return date(int(match.group(1)), 12, 31)
    return None


def parse_float(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


@dataclass
class ParseResult:
    units: list[ListingUnit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    items_found: int = 0


def _read(item, selector: str) -> str | None:
    """Text of the element a selector points to, or an attribute when written as 'selector@attribute'."""
    css, at, attribute = selector.rpartition("@")
    if not at:
        css, attribute = selector, ""
    element = item.select_one(css) if css.strip() else item
    if element is None:
        return None
    return (element.get(attribute) if attribute else element.get_text(" ", strip=True)) or None


_ALIASES = {"price": "price_etb", "image": "floor_plan_image_url", "down_payment": "down_payment_pct", "delivery": "delivery_date",
            "construction_stage": "construction_stage_pct", "rental_yield": "est_rental_yield_pct"}


def parse_listing_html(html: str, profile: ScrapeProfile, base_url: str = "") -> ParseResult:
    """Read every card matching profile.item. A card that cannot be read is reported and skipped, never fatal."""
    soup = BeautifulSoup(html, "html.parser")
    try:
        items = soup.select(profile.item)
    except Exception as exc:  # an invalid CSS selector
        raise ValueError(f"the item selector {profile.item!r} is not valid CSS") from exc
    result = ParseResult(items_found=len(items))

    for index, item in enumerate(items, start=1):
        for junk in item(["script", "style", "noscript"]):
            junk.decompose()  # their contents would otherwise be read as text
        raw: dict[str, str | None] = {name: profile.defaults.get(name) for name in profile.defaults}
        for name, selector in profile.fields.items():
            value = _read(item, selector)
            if value is not None:
                raw[_ALIASES.get(name, name)] = value
        for name in profile.defaults:
            raw.setdefault(_ALIASES.get(name, name), profile.defaults[name])

        problems = []
        price = parse_money(raw.get("price_etb"))
        sqm = parse_area(raw.get("sqm"))
        bedrooms = parse_bedrooms(raw.get("bedrooms")) if raw.get("bedrooms") else parse_bedrooms(raw.get("unit_type"))
        for label, value in (("project name", raw.get("project_name")), ("unit type", raw.get("unit_type")), ("area", raw.get("sub_city")),
                             ("price", price), ("size", sqm), ("bedrooms", bedrooms)):
            if value in (None, ""):
                problems.append(label)
        if problems:
            result.errors.append(f"item {index}: missing or unreadable {', '.join(problems)}")
            continue
        image = raw.get("floor_plan_image_url")
        try:
            result.units.append(ListingUnit(
                project_name=raw["project_name"].strip(), unit_type=raw["unit_type"].strip(), sub_city=normalize_sub_city(raw["sub_city"]),
                bedrooms=bedrooms, sqm=sqm, price_etb=price, title=(raw.get("title") or None),
                down_payment_pct=parse_percent(raw.get("down_payment_pct")), construction_stage_pct=parse_percent(raw.get("construction_stage_pct")),
                est_rental_yield_pct=parse_percent(raw.get("est_rental_yield_pct")), delivery_date=parse_date(raw.get("delivery_date")),
                lat=parse_float(raw.get("lat")), lng=parse_float(raw.get("lng")), description=(raw.get("description") or None),
                floor_plan_image_url=urljoin(base_url, image) if image else None,
            ))
        except ValidationError as exc:
            result.errors.append(f"item {index}: {exc.errors()[0]['loc'][0]} is out of range")
    return result


def page_text(html: str, limit: int = 12_000) -> str:
    """The readable text of a page, for asking the language model to extract units when there are no selectors."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:limit]
