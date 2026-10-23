from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    """The only parts of a search reply the model writes. Numbering, prices and dates are rendered by code."""

    intro: str = Field(description="One or two sentences; if the search was widened, say so openly")
    blurbs: list[str] = Field(description="One short sentence per option, in the order given, about its standout feature")
    closing: str = Field(description="One question offering to compare options or book a visit")


class Extraction(BaseModel):
    """What the router asks Gemini to pull out of the latest buyer message."""

    intent: Literal["search", "compare", "details", "book_tour", "book_consultation", "small_talk"]
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    max_budget_etb: Optional[float] = Field(default=None, description="Budget ceiling in Ethiopian birr")
    min_bedrooms: Optional[int] = None
    preferred_sub_cities: Optional[list[str]] = Field(
        default=None, description="The complete updated list of areas, or null if areas were not discussed"
    )
    search_query: Optional[str] = Field(default=None, description="English description of the home the buyer wants")
    referenced_options: list[int] = Field(default_factory=list, description="1-based numbers of listed options the buyer refers to")
    tour_start_iso: Optional[str] = Field(default=None, description="Requested visit time as ISO 8601 with UTC offset")
    ranking: Optional[Literal["cheapest", "priciest", "highest_yield", "largest"]] = Field(
        default=None,
        description="Set only when the buyer asks for the extreme of something instead of describing a home: cheapest, "
                    "priciest (most expensive or most luxurious), highest_yield (best rental income or return), largest (biggest)")
    urgency: Literal["low", "medium", "high"] = "low"
    objections: list[str] = Field(default_factory=list, description="New worries or hesitations the buyer voiced in this message, each as a short phrase")
