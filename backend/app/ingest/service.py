"""Market ingestion: from a listing page or an announcement to rows in `properties`.

Only what changed costs anything. A unit is identified by developer + project + type + size, so:
  - an unchanged unit is skipped (no model call),
  - a price change is a plain update plus a price_history row (no model call),
  - only a genuinely new unit is read by the vision model and embedded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.geo import KNOWN_SUB_CITIES, normalize_sub_city
from app.ingest.fetch import FetchError, Fetcher
from app.ingest.models import FloorPlanTags, ListingExtraction, ListingUnit, ScrapeProfile
from app.ingest.parse import ParseResult, page_text, parse_date, parse_listing_html
from app.ingest.text import content_hash, embedding_text
from app.repository import Repository

log = logging.getLogger(__name__)


class ExtractionError(Exception):
    """The language model could not read the text (quota, outage). Safe to show to the user."""


EXTRACT_SYSTEM = (
    "You read real-estate listings and announcements from developers in Addis Ababa and list the units they offer. "
    "Only include a unit when the text gives its price, its size in square metres and its bedrooms. Prices are in Ethiopian birr "
    "('14.5M' is 14500000). Use these area names where they fit: " + ", ".join(KNOWN_SUB_CITIES) + ". Never invent values; leave optional fields empty."
)
VISION_PROMPT = (
    "This is a residential floor plan. List up to 12 short, concrete features a buyer would care about, such as 'open-plan kitchen', "
    "'maid room + bath', 'corner wrap-around balcony', 'separate dining area'. If you can judge it, give the usable-to-total area ratio "
    "between 0 and 1. Describe only what the drawing shows."
)


def _pct(old: float, new: float) -> float:
    return round((new - old) / old * 100, 2)


class IngestionService:
    def __init__(self, repo: Repository, gateway: Any, fetcher: Fetcher, settings: Settings, events: Any = None):
        self.repo, self.gateway, self.fetcher, self.settings, self.events = repo, gateway, fetcher, settings, events

    # ---------------- reading the source ----------------

    async def extract_with_model(self, text: str, developer_name: str) -> tuple[list[ListingUnit], list[str]]:
        """Units read by the model, plus a note for each one that failed validation (the rest are kept)."""
        prompt = f"Developer: {developer_name}\n\nText to read:\n{text[:12_000]}"
        try:
            result = await self.gateway.generate_structured(prompt, ListingExtraction, system=EXTRACT_SYSTEM)
        except Exception:
            log.exception("model extraction failed")
            raise ExtractionError("The language model could not read this text right now. Try again in a few minutes.") from None
        units: list[ListingUnit] = []
        rejected: list[str] = []
        for raw in result.units:
            data = raw.model_dump()
            data["delivery_date"] = parse_date(data["delivery_date"])
            data["sub_city"] = normalize_sub_city(data["sub_city"])
            try:
                units.append(ListingUnit(**data))
            except ValidationError as exc:
                rejected.append(f"{raw.project_name} {raw.unit_type}: {exc.errors()[0]['loc'][0]} is missing or out of range")
        return units, rejected

    async def run(
        self, *, developer_slug: str, source: str, html: str | None = None, text: str | None = None, url: str | None = None,
        profile: ScrapeProfile | None = None, full_sync: bool = False, dry_run: bool = False,
    ) -> dict:
        """Read one source for one developer and apply it."""
        developer = await self.repo.get_developer_by_slug(developer_slug)
        if developer is None:
            raise LookupError(f"developer {developer_slug} not found")
        notes: list[str] = []
        parse_errors: list[str] = []
        base_url = url or ""

        if source == "url":
            base_url = url or developer.get("scrape_url") or ""
            if not base_url:
                raise ValueError("no address given and the developer has no scrape address saved")
            html = await self.fetcher.get_text(base_url)
        if source in ("html", "url"):
            if not html:
                raise ValueError("there is no page content to read")
            profile = profile or (ScrapeProfile(**developer["scrape_profile"]) if developer.get("scrape_profile") else None)
            if profile:
                parsed: ParseResult = parse_listing_html(html, profile, base_url)
                units, parse_errors = parsed.units, parsed.errors
                if parsed.items_found == 0:
                    notes.append("The selectors matched no unit cards on this page; the page layout may have changed.")
            else:
                notes.append("No selectors were given, so the page was read with the language model.")
                units, parse_errors = await self.extract_with_model(page_text(html), developer["name"])
        elif source == "text":
            if not text or not text.strip():
                raise ValueError("there is no announcement text to read")
            units, parse_errors = await self.extract_with_model(text, developer["name"])
        else:
            raise ValueError(f"unknown source {source!r}")

        report = await self.apply(developer, units, source=source, full_sync=full_sync, dry_run=dry_run)
        report["parse_errors"] = parse_errors
        report["notes"] = notes + report["notes"]
        return report

    # ---------------- applying units ----------------

    async def apply(self, developer: dict, units: list[ListingUnit], *, source: str, full_sync: bool, dry_run: bool) -> dict:
        report: dict[str, Any] = {
            "developer": developer["slug"], "source": source, "dry_run": dry_run, "full_sync": full_sync, "found": len(units),
            "created": [], "price_changed": [], "image_updated": [], "reactivated": [], "removed": [], "skipped": [],
            "unchanged": 0, "sync": "off" if not full_sync else "pending", "notes": [],
        }
        seen: dict[str, ListingUnit] = {}
        new_units: list[tuple[ListingUnit, str]] = []

        for unit in units:
            digest = content_hash(developer["slug"], unit.project_name, unit.unit_type, unit.sqm)
            if digest in seen:
                report["skipped"].append({"title": unit.display_title, "reason": "listed twice on the page"})
                continue
            seen[digest] = unit
            existing = await self.repo.get_property_by_hash(digest)
            if existing is None:
                new_units.append((unit, digest))
                report["created"].append({"title": unit.display_title, "price_etb": unit.price_etb, "area": unit.sub_city})
                continue
            if existing["developer_id"] != developer["id"]:
                report["skipped"].append({"title": unit.display_title, "reason": "the same unit exists under another developer"})
                continue
            changed = False
            old_price = float(existing["price_etb"])
            if abs(old_price - unit.price_etb) > 0.005:
                changed = True
                report["price_changed"].append({"title": existing["title"], "property_id": str(existing["id"]), "old_price_etb": old_price,
                                                "new_price_etb": unit.price_etb, "change_pct": _pct(old_price, unit.price_etb)})
                if not dry_run:
                    await self.repo.update_property_price(existing["id"], unit.price_etb)
            if unit.floor_plan_image_url and unit.floor_plan_image_url != existing["floor_plan_image_url"]:
                changed = True
                report["image_updated"].append({"title": existing["title"]})
                if not dry_run:
                    await self.repo.update_property_image(existing["id"], unit.floor_plan_image_url)  # keeps the vision tags: same unit
            if existing["status"] != "AVAILABLE":
                changed = True
                report["reactivated"].append({"title": existing["title"]})
                if not dry_run:
                    await self.repo.set_property_status([existing["id"]], "AVAILABLE")
            if not changed:
                report["unchanged"] += 1

        if new_units and not dry_run:
            await self._create(developer, new_units, source, report)
        if full_sync:
            await self._sync_removed(developer, seen, report, dry_run)

        counts = {"created": len(report["created"]), "price_changed": len(report["price_changed"]), "removed": len(report["removed"]),
                  "unchanged": report["unchanged"], "skipped": len(report["skipped"])}
        report["counts"] = counts
        if not dry_run:
            report["run_id"] = await self.repo.record_ingest_run(developer_id=developer["id"], source=source, dry_run=False, full_sync=full_sync, counts=counts, report=report)
            if self.events:
                self.events.publish("ingest", developer=developer["slug"], source=source, **counts)
        return report

    async def _create(self, developer: dict, new_units: list[tuple[ListingUnit, str]], source: str, report: dict) -> None:
        tags: dict[str, FloorPlanTags] = {}
        vision_budget = self.settings.ingest_max_vision_per_run
        for unit, digest in new_units:
            if not unit.floor_plan_image_url:
                continue
            if vision_budget <= 0:
                report["notes"].append(f"Skipped reading the floor plan of {unit.display_title}: the per-run limit of {self.settings.ingest_max_vision_per_run} was reached.")
                continue
            vision_budget -= 1
            try:
                image, mime = await self.fetcher.get_bytes(unit.floor_plan_image_url)
                tags[digest] = await self.gateway.describe_image(image, mime_type=mime, prompt=VISION_PROMPT, schema=FloorPlanTags)
            except Exception as exc:  # a missing or unreadable plan must never block the unit itself
                log.warning("floor plan for %s could not be read: %s", unit.display_title, type(exc).__name__)
                report["notes"].append(f"Could not read the floor plan of {unit.display_title} ({type(exc).__name__}).")

        texts = [
            embedding_text(project=u.project_name, unit_type=u.unit_type, sub_city=u.sub_city, bedrooms=u.bedrooms, sqm=u.sqm, price_etb=u.price_etb,
                           description=u.description, tags=tags[d].tags if d in tags else [])
            for u, d in new_units
        ]
        vectors = await self.gateway.embed_documents(texts)
        for (unit, digest), vector in zip(new_units, vectors):
            plan = tags.get(digest)
            coords = (unit.lat, unit.lng) if unit.lat is not None and unit.lng is not None else await self.repo.coordinates_for(developer["id"], unit.project_name, unit.sub_city)
            amenities = {"tags": plan.tags if plan else [], "source": "announcement" if source == "text" else "scrape",
                         "ingested_at": datetime.now(timezone.utc).isoformat()}
            if plan and plan.net_to_gross_ratio is not None:
                amenities["net_to_gross_ratio"] = plan.net_to_gross_ratio
            if plan and plan.summary:
                amenities["floor_plan_summary"] = plan.summary
            new_id = await self.repo.insert_property(developer_id=developer["id"], unit=unit, content_hash=digest, embedding=vector, vision_amenities=amenities,
                                                     lat=coords[0] if coords else None, lng=coords[1] if coords else None)
            for entry in report["created"]:
                if entry["title"] == unit.display_title:
                    entry["property_id"] = None if new_id is None else str(new_id)
                    entry["floor_plan_tags"] = amenities["tags"]
                    if coords is None:
                        report["notes"].append(f"{unit.display_title} has no map position (none on the page and no sibling unit to copy from).")

    async def _sync_removed(self, developer: dict, seen: dict[str, ListingUnit], report: dict, dry_run: bool) -> None:
        """Units that are no longer on the developer's page stop being offered. Guarded against a failed scrape wiping the catalogue."""
        available = await self.repo.available_for_developer(developer["id"])
        gone = [row for row in available if row["content_hash"] not in seen]
        if not seen:
            report["sync"] = "skipped: the page returned no units, so nothing was marked unavailable"
            return
        if len(available) >= 4 and len(seen) * 2 < len(available):
            report["sync"] = f"skipped: only {len(seen)} of {len(available)} known units were found, which looks like a failed scrape"
            return
        report["removed"] = [{"title": row["title"]} for row in gone]
        if gone and not dry_run:
            await self.repo.set_property_status([row["id"] for row in gone], "UNAVAILABLE")
        report["sync"] = "done" if not dry_run else "preview"
