"""Reading one listing page from a large property portal without a language model.

Portals publish hundreds of listings, so asking Gemini about each one would use the whole free quota in a morning. Most portals already
describe a listing in machine-readable form, so the fields are read in this order and the first source that has a value wins:

  1. JSON-LD blocks (schema.org RealEstateListing / Offer / floorSize),
  2. JSON the page embeds for its own scripts (server-side rendering state),
  3. CSS selectors written for that site (`SiteHints`),
  4. labelled rows ("Area | 1500", "Bedrooms: 3") and the visible text ("3 Bedrooms", "115 m²"),
  5. OpenGraph tags ("For Sale: 3 Bedroom Flat, Bole - Br 21,000,000").

A price only counts when it is *strict*: read from one of the structured sources above (never from loose page text, where price filters
and adverts live) and plausible for a home for sale in birr. A page without a strict price is the only case sent to the language model.
Everything here is a pure function: no network, no model, no database.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import ValidationError

from app.geo import KNOWN_SUB_CITIES, normalize_sub_city
from app.ingest.models import ListingUnit
from app.ingest.parse import page_text, parse_area, parse_bedrooms, parse_money

MIN_SALE_PRICE_ETB = 300_000  # below this a "sale" price is a typo, a monthly rent or a price shown in millions without its unit
MAX_SALE_PRICE_ETB = 20_000_000_000
MIN_HOME_SQM, MAX_HOME_SQM, MAX_BEDROOMS = 15, 5_000, 12  # plots of land and compounds are larger, a "2 m²" is a typo or a different figure
MIN_ETB_PER_SQM, MAX_ETB_PER_SQM = 25_000, 1_500_000
FLOOR_ETB_PER_SQM = 3_000  # below this a 'sale price' is a monthly rent or a typo (a plot of land sells for more than this per m² even far out)
RESIDENTIAL = re.compile(r"apartment|flat|condo|house|villa|penthouse|studio|duplex|townhouse|residential|g\+\d|bedroom|ቤት|አፓርታማ", re.I)
NOT_RESIDENTIAL = re.compile(r"\b(land|plot|office|shop|store|warehouse|hotel|commercial|building for rent|school|restaurant|factory)\b|ቦታ\b|ሱቅ", re.I)
RENT = re.compile(r"for[\s-]rent|/rent\b|for-rent|\brental\b|per\s+month|/month|ኪራይ|የሚከራይ", re.I)
SALE = re.compile(r"for[\s-]sale|/for-sale|/sale\b|\bsale\b|የሚሸጥ|ሽያጭ", re.I)
_AMHARIC_BEDROOMS = re.compile(r"(\d+)\s*(?:መኝታ|ክፍል)")
_TEXT_BEDROOMS = re.compile(r"(\d{1,2})\s*(?:bed(?:room)?s?|br|bdrm)\b", re.I)
_TEXT_AREA = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:m²|m2|sqm|sq\.?\s?m|square\s+met(?:er|re)s?|ካሬ)", re.I)
_CHALLENGE = re.compile(r"<title>\s*(just a moment|one moment, please|attention required|access denied|ddos-guard|checking your browser)|challenges\.cloudflare\.com|cf-chl-|_cf_chl_opt", re.I)


@dataclass(frozen=True)
class SiteHints:
    """What is special about one portal. Every field is optional: the generic readers cover most sites on their own."""

    css: dict[str, str] = field(default_factory=dict)  # field -> CSS selector, optionally 'selector@attribute' (title, price, sqm, bedrooms, sub_city, deal)
    ref_pattern: str | None = None                     # a regex on the address whose first group is the portal's own listing id


@dataclass
class PortalListing:
    url: str
    ref: str
    title: str | None = None
    price_etb: float | None = None
    sqm: float | None = None
    bedrooms: int | None = None
    sub_city: str | None = None
    property_type: str | None = None
    deal: str | None = None  # 'sale', 'rent' or None when the page does not say
    image_url: str | None = None
    description: str | None = None
    lat: float | None = None
    lng: float | None = None
    found_by: dict[str, str] = field(default_factory=dict)  # field -> where it was read, for the report and for debugging a site

    def set(self, name: str, value: Any, source: str) -> None:
        if value in (None, "") or getattr(self, name) not in (None, ""):
            return
        setattr(self, name, value)
        self.found_by[name] = source


def is_portal_unit(unit: dict) -> bool:
    """A unit read from a portal, not from a developer: nobody to send a lead registration to, so the broker is told instead."""
    return bool(unit.get("source_url")) or str(unit.get("sales_email") or "").endswith(".invalid")


def is_challenge_page(html: str) -> bool:
    """A bot-protection interstitial (Cloudflare and similar). The crawler stops for that site: such pages are not to be worked around."""
    return bool(_CHALLENGE.search(html[:20_000]))


def strict_price(value: Any) -> float | None:
    """A sale price in birr we are willing to store without asking the model, or None."""
    amount = value if isinstance(value, int | float) else parse_money(str(value)) if value not in (None, "") else None
    if amount is None or not MIN_SALE_PRICE_ETB <= float(amount) <= MAX_SALE_PRICE_ETB:
        return None
    return float(amount)


def listing_ref(url: str, hints: SiteHints | None = None) -> str:
    """The portal's own id for a listing, so the same listing is recognised on every crawl even when its title changes."""
    path = unquote(urlsplit(url).path).rstrip("/")
    if hints and hints.ref_pattern and (match := re.search(hints.ref_pattern, url)):
        return match.group(1)
    if match := re.search(r"(?:^|[/-])(\d{3,})(?:-|$)", path.rsplit("/", 1)[-1]):
        return match.group(1)
    return path.rsplit("/", 1)[-1][:60] or path


# ---------------- the readers, in order ----------------

def _json_ld(soup: BeautifulSoup) -> list[dict]:
    nodes: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                nodes.append(item)
                stack.extend(v for k, v in item.items() if k == "@graph" or isinstance(v, list))
    return nodes


def _types(node: dict) -> set[str]:
    kind = node.get("@type")
    return {k.lower() for k in (kind if isinstance(kind, list) else [kind]) if isinstance(k, str)}


_LISTING_TYPES = {"realestatelisting", "product", "offer", "residence", "apartment", "house", "singlefamilyresidence", "accommodation"}


def _read_json_ld(listing: PortalListing, soup: BeautifulSoup) -> None:
    for node in _json_ld(soup):
        if not _types(node) & _LISTING_TYPES:
            continue
        offers = node.get("offers") or {}
        offers = offers[0] if isinstance(offers, list) and offers else offers
        offer = offers if isinstance(offers, dict) else {}
        if "offer" in _types(node):
            offer = node
        currency = str(offer.get("priceCurrency") or "ETB").upper()
        if currency in ("ETB", "BIRR", "BR"):
            listing.set("price_etb", strict_price(offer.get("price")), "json-ld")
        listing.set("title", node.get("name"), "json-ld")
        size = node.get("floorSize") or {}
        listing.set("sqm", parse_area(str(size.get("value") if isinstance(size, dict) else size)) if size else None, "json-ld")
        rooms = node.get("numberOfBedrooms", node.get("numberOfRooms"))
        listing.set("bedrooms", int(rooms) if isinstance(rooms, int | float) or str(rooms).isdigit() else None, "json-ld")
        address = node.get("address") or {}
        if isinstance(address, dict):
            listing.set("sub_city", address.get("addressLocality") if address.get("addressLocality") != "Addis Ababa" else None, "json-ld")
        geo = node.get("geo") or {}
        if isinstance(geo, dict):
            listing.set("lat", _number(geo.get("latitude")), "json-ld")
            listing.set("lng", _number(geo.get("longitude")), "json-ld")
        image = node.get("image")
        listing.set("image_url", image[0] if isinstance(image, list) and image else image if isinstance(image, str) else None, "json-ld")
        listing.set("description", node.get("description"), "json-ld")


def _embedded_records(soup: BeautifulSoup) -> list[dict]:
    """Records in JSON the page hands to its own scripts (`<script type="application/json">`, Next.js data) that look like one listing."""
    found: list[dict] = []
    for script in soup.find_all("script", type="application/json"):
        raw = script.string or ""
        if raw.startswith("{&q;"):  # Angular's transfer state escapes its JSON this way
            raw = raw.replace("&q;", '"').replace("&s;", "'").replace("&l;", "<").replace("&g;", ">").replace("&a;", "&")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack and len(found) < 50:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "price" in item and ({"title", "name"} & item.keys()) and ({"area", "bedrooms", "size", "sqm", "location", "status"} & item.keys()):
                    found.append(item)
                stack.extend(v for v in item.values() if isinstance(v, dict | list))
    return found


def _name_of(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("slug")
    if isinstance(value, list) and value:
        return _name_of(value[0])
    return str(value) if value not in (None, "") else None


def _read_embedded(listing: PortalListing, soup: BeautifulSoup) -> None:
    for record in _embedded_records(soup)[:1]:
        prefix = str(record.get("price_prefix") or record.get("currency") or "ETB").upper()
        if prefix in ("ETB", "BIRR", "BR", "BR."):
            listing.set("price_etb", strict_price(record.get("price")), "embedded-json")
        listing.set("title", (record.get("title") or record.get("name") or "").strip() or None, "embedded-json")
        listing.set("sqm", parse_area(str(record.get("area") or record.get("size") or record.get("sqm") or "")), "embedded-json")
        beds = record.get("bedrooms")
        listing.set("bedrooms", int(beds) if isinstance(beds, int) or str(beds).isdigit() else None, "embedded-json")
        listing.set("sub_city", _name_of(record.get("location")), "embedded-json")
        listing.set("property_type", _name_of(record.get("type")), "embedded-json")
        status = (_name_of(record.get("status")) or "").lower()
        listing.set("deal", "rent" if RENT.search(status) else "sale" if SALE.search(status) else None, "embedded-json")
        listing.set("lat", _number(record.get("latitude")), "embedded-json")
        listing.set("lng", _number(record.get("longitude")), "embedded-json")
        content = record.get("content") or record.get("description")
        listing.set("description", BeautifulSoup(content, "html.parser").get_text(" ", strip=True) if isinstance(content, str) else None, "embedded-json")


def _select(soup: BeautifulSoup, selector: str) -> str | None:
    css, at, attribute = selector.rpartition("@")
    if not at:
        css, attribute = selector, ""
    try:
        element = soup.select_one(css)
    except Exception:  # a broken selector in a site's hints must not stop the generic readers
        return None
    if element is None:
        return None
    value = element.get(attribute) if attribute else element.get_text(" ", strip=True)
    return str(value).strip() if value else None


def _read_css(listing: PortalListing, soup: BeautifulSoup, hints: SiteHints) -> None:
    for name, selector in hints.css.items():
        raw = _select(soup, selector)
        if raw is None:
            continue
        if name == "price":
            listing.set("price_etb", strict_price(raw), "css")
        elif name == "sqm":
            listing.set("sqm", parse_area(raw), "css")
        elif name == "bedrooms":
            listing.set("bedrooms", parse_bedrooms(raw) if not raw.isdigit() else int(raw), "css")
        elif name == "deal":
            listing.set("deal", "rent" if RENT.search(raw) else "sale" if SALE.search(raw) else None, "css")
        elif name in ("title", "sub_city", "property_type", "image_url", "description"):
            listing.set(name, raw, "css")


_LABELS = {
    "sqm": re.compile(r"^(total\s+)?(area|size|floor\s*area|built[\s-]*up|covered\s+area|ስፋት)\b", re.I),
    "bedrooms": re.compile(r"^(bed(room)?s?|no\.?\s+of\s+bed)", re.I),
    "sub_city": re.compile(r"^(sub[\s-]?city|neighbou?rhood|location|area\s+name|ክ/ከተማ|ሰፈር)\b", re.I),
    "property_type": re.compile(r"^(property\s+)?type\b", re.I),
}


def _labelled_rows(soup: BeautifulSoup) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            rows.append((cells[0].rstrip(": "), cells[1]))
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            rows.append((dt.get_text(" ", strip=True).rstrip(": "), dd.get_text(" ", strip=True)))
    return rows


def _read_labels_and_text(listing: PortalListing, soup: BeautifulSoup, text: str) -> None:
    for label, value in _labelled_rows(soup):
        if not value:
            continue
        for name, pattern in _LABELS.items():
            if not pattern.search(label):
                continue
            if name == "sqm":
                listing.set("sqm", parse_area(value), "label")
            elif name == "bedrooms":
                listing.set("bedrooms", int(value) if value.isdigit() else parse_bedrooms(value), "label")
            else:
                listing.set(name, value, "label")
    # Loose page text also holds sidebars and statistics ("226,000 m²" of a district), so only plausible home figures count.
    for pattern in (_TEXT_BEDROOMS, _AMHARIC_BEDROOMS):
        for match in pattern.finditer(text):
            if 1 <= int(match.group(1)) <= MAX_BEDROOMS:
                listing.set("bedrooms", int(match.group(1)), "text")
                break
    for match in _TEXT_AREA.finditer(text):
        sqm = parse_area(match.group(1))
        if sqm and MIN_HOME_SQM <= sqm <= MAX_HOME_SQM:
            listing.set("sqm", sqm, "text")
            break
    if "studio" in (listing.title or "").lower():
        listing.set("bedrooms", 0, "text")


def _read_open_graph(listing: PortalListing, soup: BeautifulSoup) -> None:
    def meta(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return str(tag["content"]).strip() if tag and tag.get("content") else None

    title = meta("og:title") or ""
    # The price in an OpenGraph title is only trusted when it is marked as birr: "- Br 21,000,000" or "— 5,000,000 ETB".
    if match := re.search(r"(?:\bBr\.?|\bETB|\bBirr)\s*([\d][\d,.]*\s*(?:million|m)?)\b|([\d][\d,.]*\s*(?:million|m)?)\s*(?:ETB|Birr|ብር)\b", title, re.I):
        listing.set("price_etb", strict_price(match.group(1) or match.group(2)), "open-graph")
    for amount, currency in (("product:price:amount", "product:price:currency"), ("og:price:amount", "og:price:currency")):
        if (meta(currency) or "ETB").upper() == "ETB":
            listing.set("price_etb", strict_price(meta(amount)), "open-graph")
    cleaned = re.split(r"\s+[-—|]\s+(?:Br|ETB|\d)", re.sub(r"^(for sale|for rent)\s*:\s*", "", title, flags=re.I))[0].strip()
    listing.set("title", cleaned or None, "open-graph")
    listing.set("image_url", meta("og:image"), "open-graph")
    listing.set("description", meta("og:description"), "open-graph")


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


_AMHARIC_AREAS = {"ቦሌ": "Bole", "ሲኤምሲ": "CMC", "ካዛንቺስ": "Kazanchis", "አያት": "Ayat", "ሳርቤት": "Sarbet", "ለቡ": "Lebu", "ሰሚት": "Summit",
                  "ገርጂ": "Gerji", "መገናኛ": "Megenagna", "ጀሞ": "Jemo", "ኮተቤ": "Kotebe", "ሃያት": "Ayat"}


def _sub_city_from(location: str | None, url: str, title: str | None = None) -> str | None:
    """'Summit, Bole, Addis Ababa' -> 'Bole' (a known area wins), else the first part that is not the city. Amharic names count too."""
    candidates = [location or "", unquote(urlsplit(url).path).replace("-", " "), title or ""]
    for text in candidates:
        for known in KNOWN_SUB_CITIES:
            if re.search(rf"\b{re.escape(known)}\b", text, re.I):
                return known
        for amharic, english in _AMHARIC_AREAS.items():
            if amharic in text:
                return english
    for part in (location or "").split(","):
        part = part.strip()
        if part and part.lower() not in ("addis ababa", "ethiopia", "addis abeba", "aa"):
            return normalize_sub_city(part)
    return None


def extract_listing(html: str, url: str, hints: SiteHints | None = None) -> PortalListing:
    """Every field the page states, read locally. Missing fields stay None; nothing is guessed."""
    hints = hints or SiteHints()
    soup = BeautifulSoup(html, "html.parser")
    listing = PortalListing(url=url, ref=listing_ref(url, hints))
    _read_json_ld(listing, soup)
    _read_embedded(listing, soup)
    _read_css(listing, soup, hints)
    text = page_text(html, 20_000)
    _read_labels_and_text(listing, soup, text)
    _read_open_graph(listing, soup)

    if listing.found_by.get("sqm") == "text" and listing.price_etb and listing.sqm:
        if not MIN_ETB_PER_SQM <= listing.price_etb / listing.sqm <= MAX_ETB_PER_SQM:  # a size read from loose text must fit the price, or it is dropped
            listing.sqm = None
            listing.found_by.pop("sqm")

    signals = f"{url} {listing.title or ''} {soup.title.get_text() if soup.title else ''}"
    listing.set("deal", "rent" if RENT.search(signals) else "sale" if SALE.search(signals) else None, "url-or-title")
    listing.sub_city = _sub_city_from(listing.sub_city, url, listing.title)
    if listing.image_url:
        listing.image_url = urljoin(url, listing.image_url)
    return listing


# ---------------- from a listing to a unit ----------------

def unit_type_for(listing: PortalListing) -> str:
    kind = (listing.property_type or "").strip()
    if not kind:
        title = (listing.title or "").lower()
        kind = next((k.title() for k in ("villa", "penthouse", "duplex", "townhouse", "condominium", "house", "apartment", "flat", "studio") if k in title), "Home")
    if listing.bedrooms == 0:
        return "Studio"
    return f"{listing.bedrooms}BR {kind.title()}" if listing.bedrooms else kind.title()


def project_name_for(listing: PortalListing, portal_name: str) -> str:
    """Stable across crawls: the portal's listing id is part of it, so a retitled listing is still the same unit."""
    title = re.sub(r"\s+", " ", listing.title or "Listing").strip()[:80]
    return f"{title} ({portal_name} #{listing.ref})"


def skip_reason(listing: PortalListing) -> str | None:
    """Why this listing is not something the broker sells (a rental, land, an office), or None."""
    if listing.deal == "rent":
        return "a rental, not a home for sale"
    kind = f"{listing.property_type or ''} {listing.title or ''}"
    if NOT_RESIDENTIAL.search(kind) and not RESIDENTIAL.search(listing.property_type or ""):
        return "not a home (land, office, shop or similar)"
    return None


def implausible_price(price_etb: float | None, sqm: float | None) -> str | None:
    """A reason to refuse a price whatever read it (a page reader or the language model), or None."""
    if price_etb and sqm and price_etb / sqm < FLOOR_ETB_PER_SQM:
        return f"the price works out to {price_etb / sqm:,.0f} ETB per m², far too low for a sale: probably a rent or a typo"
    return None


def to_unit(listing: PortalListing, portal_name: str) -> ListingUnit | str:
    """A unit ready for IngestionService.apply, or the reason it cannot be one."""
    if reason := skip_reason(listing):
        return reason
    missing = [label for label, value in (("price", listing.price_etb), ("size", listing.sqm), ("bedrooms", listing.bedrooms), ("area", listing.sub_city))
               if value is None]
    if missing:
        return f"missing {', '.join(missing)}"
    if reason := implausible_price(listing.price_etb, listing.sqm):
        return reason
    try:
        return ListingUnit(
            project_name=project_name_for(listing, portal_name), unit_type=unit_type_for(listing), sub_city=listing.sub_city or "",
            bedrooms=listing.bedrooms or 0, sqm=listing.sqm or 0, price_etb=listing.price_etb or 0, title=(listing.title or None),
            description=(listing.description or "")[:2000] or None, lat=listing.lat, lng=listing.lng, floor_plan_image_url=None, source_url=listing.url,
        )
    except ValidationError as exc:
        return f"{exc.errors()[0]['loc'][0]} is out of range"
