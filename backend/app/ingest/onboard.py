"""Onboard a developer from a page address: the language model proposes where each field lives, and the proposal is tested at once.

Nothing is saved here. The caller shows the broker how many units the proposal found and what they look like, and the broker decides.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

from app.ingest.models import ScrapeProfile
from app.ingest.parse import parse_listing_html

FIELDS = ("project_name", "unit_type", "sub_city", "price", "sqm", "bedrooms", "down_payment", "delivery", "description", "image")
SYSTEM = (
    "You write CSS selectors for a real-estate listing page. The page shows one card per unit. Reply with: `item`, a selector that "
    "matches exactly one element per unit card; and `fields`, a mapping from field name to a selector relative to the card "
    f"(append @attribute to read an attribute such as @src). Known fields: {', '.join(FIELDS)}. Required: project_name, unit_type, "
    "price and sqm; bedrooms may be left out (it is read from the unit type). Put values that are the same for the whole page, such as "
    "its area, in `defaults`. Use only selectors that appear in the HTML you are given."
)


class ProfileSuggestion(BaseModel):
    item: str = Field(description="CSS selector matching one element per unit card")
    fields: dict[str, str] = Field(default_factory=dict, description="field name -> selector inside the card")
    defaults: dict[str, str] = Field(default_factory=dict, description="values that are the same for every unit on the page")
    reasoning: str = ""


def skeleton(html: str, limit: int = 9000) -> str:
    """The page reduced to its structure and visible text: scripts, styles and attributes other than class, id and href are dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "head", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(True):
        keep = {k: v for k, v in tag.attrs.items() if k in ("class", "id", "src", "href", "data-price", "data-sqm")}
        tag.attrs = keep
    text = re.sub(r"\s+", " ", str(soup))
    return text[:limit]


async def suggest_profile(gateway: Any, html: str, developer_name: str, base_url: str = "") -> dict:
    """Ask for selectors, try them on the page, and report what they found. Never raises for a bad proposal."""
    prompt = f"Developer: {developer_name}\n\nPage HTML (shortened):\n{skeleton(html)}"
    suggestion = await gateway.generate_structured(prompt, ProfileSuggestion, system=SYSTEM)
    try:
        profile = ScrapeProfile(item=suggestion.item, fields=suggestion.fields, defaults=suggestion.defaults)
        parsed = parse_listing_html(html, profile, base_url)
    except (ValidationError, ValueError, Exception) as exc:  # a selector the parser rejects is a failed proposal, not a failed request
        return {"profile": suggestion.model_dump(exclude={"reasoning"}), "reasoning": suggestion.reasoning, "found": 0, "items_found": 0,
                "preview": [], "errors": [f"the proposed selectors could not be used: {type(exc).__name__}"], "usable": False}
    return {
        "profile": {"item": profile.item, "fields": profile.fields, "defaults": profile.defaults}, "reasoning": suggestion.reasoning,
        "found": len(parsed.units), "items_found": parsed.items_found, "errors": parsed.errors[:5], "usable": bool(parsed.units),
        "preview": [{"title": u.display_title, "sub_city": u.sub_city, "bedrooms": u.bedrooms, "sqm": u.sqm, "price_etb": u.price_etb} for u in parsed.units[:5]],
    }
