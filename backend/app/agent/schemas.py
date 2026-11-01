from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    """The only parts of a search reply the model writes. Numbering, prices and dates are rendered by code."""

    intro: str = Field(description="One or two sentences; if the search was widened, say so openly")
    blurbs: list[str] = Field(description="One short sentence per option, in the order given, about its standout feature")
    closing: str = Field(description="One question offering to compare options or book a visit")


class Extraction(BaseModel):
    """What the router asks Gemini to pull out of the latest buyer message."""

    intent: Literal["search", "compare", "details", "affordability", "book_tour", "book_consultation", "small_talk"]
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    max_budget_etb: float | None = Field(default=None, description="Budget ceiling in Ethiopian birr")
    monthly_budget_etb: float | None = Field(default=None, description="What the buyer says they can pay per month, in birr")
    min_bedrooms: int | None = None
    preferred_sub_cities: list[str] | None = Field(
        default=None, description="The complete updated list of areas, or null if areas were not discussed"
    )
    area_is_firm: bool | None = Field(
        default=None,
        description="True when the buyer insists on the area ('only in X', 'not in Y', 'I need X'); false when they say other areas are fine; null if not said")
    search_query: str | None = Field(default=None, description="English description of the home the buyer wants")
    referenced_options: list[int] = Field(default_factory=list, description="1-based numbers of listed options the buyer refers to")
    tour_start_iso: str | None = Field(default=None, description="Requested visit time as ISO 8601 with UTC offset")
    ranking: Literal["cheapest", "priciest", "highest_yield", "largest"] | None = Field(
        default=None,
        description="Set only when the buyer asks for the extreme of something instead of describing a home: cheapest, "
                    "priciest (most expensive or most luxurious), highest_yield (best rental income or return), largest (biggest)")
    urgency: Literal["low", "medium", "high"] = "low"
    objections: list[str] = Field(default_factory=list, description="New worries or hesitations the buyer voiced in this message, each as a short phrase")
