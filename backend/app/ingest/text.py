from __future__ import annotations

import hashlib


def content_hash(slug: str, project: str, unit_type: str, sqm: float) -> str:
    """Identity of a unit model: stable across price changes, so a price edit updates the same row."""
    key = "|".join([slug.strip().lower(), project.strip().lower(), unit_type.strip().lower(), f"{float(sqm):g}"])
    return hashlib.sha256(key.encode()).hexdigest()


def embedding_text(*, project: str, unit_type: str, sub_city: str, bedrooms: int, sqm: float, price_etb: float, description: str | None, tags: list[str]) -> str:
    """What gets embedded for search. Same layout as the seed data so old and new units sit in one vector space."""
    return (
        f"{project} - {unit_type} in {sub_city}, Addis Ababa. {bedrooms} bedrooms, {sqm:g} sqm, {price_etb:,.0f} ETB. "
        f"{description or ''} Features: {', '.join(tags)}."
    ).replace("  ", " ")
