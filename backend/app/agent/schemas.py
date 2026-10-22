from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Extraction(BaseModel):
    """What the router asks Gemini to pull out of the latest buyer message."""

    intent: Literal["search", "compare", "book_tour", "small_talk"]
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
    urgency: Literal["low", "medium", "high"] = "low"
