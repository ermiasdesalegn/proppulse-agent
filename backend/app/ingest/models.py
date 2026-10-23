from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class ListingUnit(BaseModel):
    """One unit model as a developer advertises it. This is also what the language model is asked to produce."""

    project_name: str = Field(min_length=1)
    unit_type: str = Field(min_length=1, description="For example '3BR Apartment', 'Studio', '4BR Villa'")
    sub_city: str = Field(min_length=1, description="Neighbourhood, for example Bole, CMC, Kazanchis, Ayat, Sarbet, Lebu")
    bedrooms: int = Field(ge=0, le=20, description="0 for a studio")
    sqm: float = Field(gt=0, lt=100_000)
    price_etb: float = Field(gt=0, lt=1e12, description="Total price in Ethiopian birr")
    title: Optional[str] = None
    down_payment_pct: Optional[float] = Field(default=None, ge=0, le=100)
    construction_stage_pct: Optional[float] = Field(default=None, ge=0, le=100)
    est_rental_yield_pct: Optional[float] = Field(default=None, ge=0, le=100)
    delivery_date: Optional[date] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    description: Optional[str] = None
    floor_plan_image_url: Optional[str] = None

    @property
    def display_title(self) -> str:
        return self.title or f"{self.project_name} {self.unit_type}"


class ExtractedUnit(BaseModel):
    """The shape the language model is asked for. It deliberately has no numeric limits: Gemini's structured-output schema
    rejects them (for example `exclusiveMinimum`). Each unit is validated against ListingUnit afterwards."""

    project_name: str
    unit_type: str = Field(description="For example '3BR Apartment', 'Studio', '4BR Villa'")
    sub_city: str = Field(description="Neighbourhood, for example Bole, CMC, Kazanchis, Ayat, Sarbet, Lebu")
    bedrooms: int = Field(description="0 for a studio")
    sqm: float
    price_etb: float = Field(description="Total price in Ethiopian birr")
    title: Optional[str] = None
    down_payment_pct: Optional[float] = None
    construction_stage_pct: Optional[float] = None
    est_rental_yield_pct: Optional[float] = None
    delivery_date: Optional[str] = Field(default=None, description="As written, for example 'June 2027' or 'Q3 2027'")
    lat: Optional[float] = None
    lng: Optional[float] = None
    description: Optional[str] = None
    floor_plan_image_url: Optional[str] = None


class ListingExtraction(BaseModel):
    units: list[ExtractedUnit] = Field(default_factory=list)


class FloorPlanTags(BaseModel):
    """What the vision model reads from a floor-plan image."""

    tags: list[str] = Field(default_factory=list, description="Up to 12 short features, for example 'open-plan kitchen', 'maid room + bath', 'corner wrap-around balcony'")
    net_to_gross_ratio: Optional[float] = Field(default=None, description="Usable area divided by total area as a number between 0 and 1, if it can be judged")
    summary: str = ""


class ScrapeProfile(BaseModel):
    """Where each field lives on a developer's listing page. A selector may end with @attribute to read an attribute."""

    item: str = Field(min_length=1, description="CSS selector matching one card per unit")
    fields: dict[str, str] = Field(default_factory=dict, description="field name -> CSS selector inside the card")
    defaults: dict[str, str] = Field(default_factory=dict, description="values used when a field is missing, for example the sub_city of a single-area page")
