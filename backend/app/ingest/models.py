from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ListingUnit(BaseModel):
    """One unit model as a developer advertises it. This is also what the language model is asked to produce."""

    project_name: str = Field(min_length=1)
    unit_type: str = Field(min_length=1, description="For example '3BR Apartment', 'Studio', '4BR Villa'")
    sub_city: str = Field(min_length=1, description="Neighbourhood, for example Bole, CMC, Kazanchis, Ayat, Sarbet, Lebu")
    bedrooms: int = Field(ge=0, le=20, description="0 for a studio")
    sqm: float = Field(gt=0, lt=100_000)
    price_etb: float = Field(gt=0, lt=1e12, description="Total price in Ethiopian birr")
    title: str | None = None
    down_payment_pct: float | None = Field(default=None, ge=0, le=100)
    construction_stage_pct: float | None = Field(default=None, ge=0, le=100)
    est_rental_yield_pct: float | None = Field(default=None, ge=0, le=100)
    delivery_date: date | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    floor_plan_image_url: str | None = None
    source_url: str | None = Field(default=None, max_length=2048, description="Where the unit is listed (a portal listing page); never sent to buyers")

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
    title: str | None = None
    down_payment_pct: float | None = None
    construction_stage_pct: float | None = None
    est_rental_yield_pct: float | None = None
    delivery_date: str | None = Field(default=None, description="As written, for example 'June 2027' or 'Q3 2027'")
    lat: float | None = None
    lng: float | None = None
    description: str | None = None
    floor_plan_image_url: str | None = None


class ListingExtraction(BaseModel):
    units: list[ExtractedUnit] = Field(default_factory=list)


class FloorPlanTags(BaseModel):
    """What the vision model reads from a floor-plan image."""

    tags: list[str] = Field(default_factory=list, description="Up to 12 short features, for example 'open-plan kitchen', 'maid room + bath', 'corner wrap-around balcony'")
    net_to_gross_ratio: float | None = Field(default=None, description="Usable area divided by total area as a number between 0 and 1, if it can be judged")
    summary: str = ""


class PortalItem(BaseModel):
    """One listing page from a property portal, as the Scrapy crawler read it locally. `text` is only sent when no strict price was found,
    so the backend can ask the language model about that page and no other."""

    url: str = Field(min_length=8, max_length=2048, pattern=r"^https?://")
    ref: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=300)
    price_etb: float | None = Field(default=None, gt=0, lt=1e12)
    sqm: float | None = Field(default=None, gt=0, lt=100_000)
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    sub_city: str | None = Field(default=None, max_length=80)
    property_type: str | None = Field(default=None, max_length=80)
    deal: str | None = Field(default=None, pattern=r"^(sale|rent)$")
    description: str | None = Field(default=None, max_length=4000)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    text: str | None = Field(default=None, max_length=12_000)
    found_by: dict[str, str] = Field(default_factory=dict)


class ScrapeProfile(BaseModel):
    """Where each field lives on a developer's listing page. A selector may end with @attribute to read an attribute."""

    item: str = Field(min_length=1, description="CSS selector matching one card per unit")
    fields: dict[str, str] = Field(default_factory=dict, description="field name -> CSS selector inside the card")
    defaults: dict[str, str] = Field(default_factory=dict, description="values used when a field is missing, for example the sub_city of a single-area page")
