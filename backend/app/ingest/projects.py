"""Reading a developer's whole site for *projects*, for developers that publish no unit prices.

Two steps, both safe by construction:
 1. a small breadth-first crawl of one website (same host only, page and depth caps, robots.txt and pacing through the shared fetcher);
 2. one model call per page that looks like a project page, asking for the project's name, area, sizes, floors, status and any offer.

Nothing here prices anything. A project's price stays empty ("price on request") until the broker enters it.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from app.geo import normalize_sub_city
from app.ingest.fetch import FetchError

log = logging.getLogger(__name__)

SKIP_PATH = re.compile(r"/(blog|news|career|careers|contact|about|privacy|terms|login|register|tag|category|feed|wp-json|wp-admin|cart|search)(/|$)|\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|css|js)(\?|$)", re.I)
PROJECT_WORDS = re.compile(r"\b(floors?|bedrooms?|sqm|sq\.?\s?m|m²|apartments?|villas?|townhouses?|for sale|selling|sold out|residential|condominium)\b", re.I)
OFFER_WORDS = re.compile(r"\b(discount|offer|promo|promotion|limited time|special price|% off|percent off|installment|free)\b", re.I)

SYSTEM = (
    "You read the text of one web page from a real-estate developer's site and say whether it describes ONE residential project for sale, and if so "
    "what it says. Use only what the text says. Never guess a price: this tool does not collect prices. If the page lists several projects, "
    "or is a news, about or contact page, set is_project_page to false. status is SELLING unless the page says sold out (SOLD_OUT) or coming "
    "soon (UPCOMING). promotion is the developer's own wording of a current offer or discount, empty if there is none; promotion_until is "
    "an ISO date (YYYY-MM-DD) only if the page gives an end date."
)


class ProjectInfo(BaseModel):
    """What the model is asked for. No numeric limits: Gemini's structured-output schema rejects them, so values are checked afterwards."""

    is_project_page: bool = Field(description="True only if this page describes a single residential project for sale")
    name: str = ""
    sub_city: str = Field(default="", description="Neighbourhood, for example Bole, CMC, Kazanchis, Ayat, Sarbet, Lebu, Meri")
    location_text: str = Field(default="", description="Where it is, in the page's own words, one short sentence")
    summary: str = Field(default="", description="What makes the project appealing, one or two sentences from the page, no prices")
    bedrooms_min: int | None = None
    bedrooms_max: int | None = None
    sqm_min: float | None = None
    sqm_max: float | None = None
    floors: int | None = None
    status: str = "SELLING"
    promotion: str = ""
    promotion_discount_pct: float | None = None
    promotion_until: str = ""


@dataclass
class Page:
    url: str
    text: str
    image: str | None


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        junk.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def page_image(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, str(og["content"]))
    for img in soup.find_all("img"):
        src = str(img.get("src") or img.get("data-src") or "").strip()
        if src and not src.startswith("data:") and not src.lower().split("?")[0].endswith((".svg", ".gif", ".ico")):
            return urljoin(base_url, src)
    return None


def links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        target = urldefrag(urljoin(base_url, str(a["href"])))[0]
        if urlparse(target).scheme in ("http", "https"):
            found.append(target)
    return found


async def crawl(fetcher: Any, start_url: str, *, max_pages: int = 40, max_depth: int = 2) -> tuple[list[Page], list[str]]:
    """Breadth-first over one site. Returns the pages read and short notes about pages that could not be."""
    host, seen, notes = _host(start_url), {start_url}, []
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    pages: list[Page] = []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        try:
            html = await fetcher.get_text(url)
        except FetchError as exc:
            notes.append(f"{url}: {exc}")
            continue
        pages.append(Page(url=url, text=page_text(html), image=page_image(html, url)))
        if depth >= max_depth:
            continue
        for target in links(html, url):
            if _host(target) == host and target not in seen and not SKIP_PATH.search(urlparse(target).path):
                seen.add(target)
                queue.append((target, depth + 1))
    return pages, notes


def looks_like_a_project(page: Page) -> bool:
    return len(page.text) >= 400 and len(PROJECT_WORDS.findall(page.text)) >= 3


GENERIC_WORDS = {"apartment", "apartments", "luxury", "standard", "residential", "project"}


def slugify(name: str) -> str:
    """Identity of a project across pages: generic words are dropped, so "Sarbet Gabriel Luxury Apartments" and "Sarbet Gabriel" are one project."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in GENERIC_WORDS]
    return "-".join(words) or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def page_hash(page: Page) -> str:
    return hashlib.sha256(page.text.encode()).hexdigest()


def _status(value: str) -> str:
    v = (value or "").strip().upper().replace(" ", "_")
    return v if v in ("SELLING", "SOLD_OUT", "UPCOMING") else "SELLING"


def _positive(value: Any, upper: float) -> Any:
    return value if isinstance(value, int | float) and 0 < value <= upper else None


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value and value.strip() else None
    except ValueError:
        return None


def to_row(info: ProjectInfo, page: Page) -> dict | None:
    """Check what the model said and shape it for the database. None means the page is not a usable project."""
    name = info.name.strip()
    if not info.is_project_page or len(name) < 3:
        return None
    pct = _positive(info.promotion_discount_pct, 100)
    promo = info.promotion.strip() or None
    low, high = _positive(info.bedrooms_min, 20), _positive(info.bedrooms_max, 20)
    if low is not None and high is not None and low > high:
        low, high = high, low
    smin, smax = _positive(info.sqm_min, 20_000), _positive(info.sqm_max, 20_000)
    if smin is not None and smax is not None and smin > smax:
        smin, smax = smax, smin
    return {
        "name": name, "slug": slugify(name), "source_url": page.url, "sub_city": normalize_sub_city(info.sub_city) if info.sub_city.strip() else None,
        "location_text": info.location_text.strip() or None, "summary": info.summary.strip() or None, "bedrooms_min": low, "bedrooms_max": high,
        "sqm_min": smin, "sqm_max": smax, "floors": _positive(info.floors, 200), "status": _status(info.status), "image_url": page.image,
        "promo_text": promo, "promo_discount_pct": pct if promo else None, "promo_until": _iso(info.promotion_until) if promo else None,
        "content_hash": page_hash(page),
    }


async def read_projects(gateway: Any, pages: list[Page], *, known: dict[str, str] | None = None, max_model_calls: int = 20) -> tuple[list[dict], list[str]]:
    """Ask the model about each candidate page. `known` maps source_url -> content hash, so unchanged pages cost nothing."""
    rows: list[dict] = []
    notes: list[str] = []
    calls = 0
    for page in pages:
        if not looks_like_a_project(page):
            continue
        if known and known.get(page.url) == page_hash(page):
            rows.append({"source_url": page.url, "content_hash": page_hash(page), "unchanged": True})
            continue
        if calls >= max_model_calls:
            notes.append("Stopped reading pages: the per-crawl limit was reached. Run it again to continue.")
            break
        calls += 1
        try:
            info = await gateway.generate_structured(f"Page address: {page.url}\n\nPage text:\n{page.text[:6000]}", ProjectInfo, system=SYSTEM)
        except Exception as exc:
            log.warning("could not read project page %s (%s)", page.url, type(exc).__name__)
            notes.append(f"{page.url}: the language model could not read it right now")
            continue
        row = to_row(info, page)
        if row is not None:
            rows.append(row)
    return rows, notes
