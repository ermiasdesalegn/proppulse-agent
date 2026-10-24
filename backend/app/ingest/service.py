"""Market ingestion: from a listing page or an announcement to rows in `properties`.

Only what changed costs anything. A unit is identified by developer + project + type + size, so:
  - an unchanged unit is skipped (no model call),
  - a price change is a plain update plus a price_history row (no model call),
  - only a genuinely new unit is read by the vision model and embedded.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.geo import KNOWN_SUB_CITIES, normalize_sub_city
from app.ingest.fetch import Fetcher, FetchError
from app.ingest.models import FloorPlanTags, ListingExtraction, ListingUnit, PortalItem, ScrapeProfile
from app.ingest.parse import ParseResult, page_text, parse_date, parse_listing_html
from app.ingest.portal import MAX_SALE_PRICE_ETB, MIN_SALE_PRICE_ETB, PortalListing, project_name_for, skip_reason, to_unit
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
    def __init__(self, repo: Repository, gateway: Any, fetcher: Fetcher, settings: Settings, events: Any = None, browser: Fetcher | None = None):
        self.repo, self.gateway, self.fetcher, self.settings, self.events = repo, gateway, fetcher, settings, events
        self.browser = browser  # renders JavaScript; None when Playwright is not set up

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
        floor_plan: tuple[bytes, str] | None = None,
    ) -> dict:
        """Read one source for one developer and apply it."""
        developer = await self.repo.get_developer_by_slug(developer_slug)
        if developer is None:
            raise LookupError(f"developer {developer_slug} not found")
        notes: list[str] = []
        parse_errors: list[str] = []
        items_found: int | None = None
        base_url = url or ""

        if source == "url":
            base_url = url or developer.get("scrape_url") or ""
            if not base_url:
                raise ValueError("no address given and the developer has no scrape address saved")
            mode = developer.get("fetch_mode") or "auto"
            if mode == "browser" and self.browser is None:
                raise FetchError("this developer needs the browser renderer, which is not set up (see the README)")
            html = await (self.browser if mode == "browser" else self.fetcher).get_text(base_url)
        if source in ("html", "url"):
            if not html:
                raise ValueError("there is no page content to read")
            profile = profile or (ScrapeProfile(**developer["scrape_profile"]) if developer.get("scrape_profile") else None)
            if profile:
                parsed: ParseResult = parse_listing_html(html, profile, base_url)
                units, parse_errors, items_found = parsed.units, parsed.errors, parsed.items_found
                if parsed.items_found == 0 and source == "url" and (developer.get("fetch_mode") or "auto") == "auto" and self.browser is not None:
                    try:  # the cards may be built by JavaScript: look again after the page has run its scripts
                        rendered = await self.browser.get_text(base_url)
                        again = parse_listing_html(rendered, profile, base_url)
                        if again.items_found:
                            parsed, notes = again, [*notes, "The page builds its listings with JavaScript, so it was read in a browser."]
                            units, parse_errors, items_found = again.units, again.errors, again.items_found
                    except FetchError as exc:
                        notes.append(f"The page had no unit cards and the browser could not be used ({exc}).")
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

        report = await self.apply(developer, units, source=source, full_sync=full_sync, dry_run=dry_run, floor_plan=floor_plan, items_found=items_found)
        report["parse_errors"] = parse_errors
        report["notes"] = notes + report["notes"]
        return report

    async def run_portal(self, *, portal: str, name: str, items: list[PortalItem], dry_run: bool = False, llm_budget: int = 3) -> dict:
        """A batch of listings a portal crawler read locally. Listings with a strict price cost nothing; only a page without one is read by
        the model, at most `llm_budget` times per batch. A portal is stored as a developer with no website and an undeliverable address, so
        the daily LLM sweep never visits it and no lead-registration email is sent to the portal."""
        developer = await self.repo.get_developer_by_slug(portal)
        if developer is None:
            if dry_run:
                developer = {"id": None, "slug": portal, "name": name}
            else:
                await self.repo.create_developer(name=name, slug=portal, sales_email=f"listings@{portal}.invalid", website_url=None)
                developer = await self.repo.get_developer_by_slug(portal)
                assert developer is not None
        units: list[ListingUnit] = []
        skipped: list[dict] = []
        model_reads = 0
        for item in items:
            listing = PortalListing(**item.model_dump(exclude={"text"}))
            if listing.price_etb is not None and (listing.price_etb < MIN_SALE_PRICE_ETB or listing.price_etb > MAX_SALE_PRICE_ETB):
                listing.price_etb = None  # the crawler's reading is checked again here: never trust a client
            if listing.price_etb is None and not skip_reason(listing) and item.text and model_reads < llm_budget:
                model_reads += 1
                try:
                    read, rejected = await self.extract_with_model(f"{item.title or ''}\n{item.text}", name)
                except ExtractionError as exc:
                    skipped.append({"title": item.title or item.url, "url": item.url, "reason": str(exc)})
                    continue
                for unit in read:  # the model may find several units on one page; the stable name keeps each recognisable next time
                    units.append(unit.model_copy(update={"project_name": project_name_for(listing, name), "title": unit.title or item.title}))
                if not read:
                    skipped.append({"title": item.title or item.url, "url": item.url, "reason": "; ".join(rejected) or "the model found no priced home on the page"})
                continue
            result = to_unit(listing, name)
            if isinstance(result, str):
                if listing.price_etb is None and not skip_reason(listing) and item.text:
                    result = f"{result} (model budget of {llm_budget} for this batch used up)"
                skipped.append({"title": item.title or item.url, "url": item.url, "reason": result})
            else:
                units.append(result)
        if developer["id"] is None:  # a preview of a portal not seen before: nothing to compare against, everything would be new
            return {"developer": portal, "source": "portal", "dry_run": True, "found": len(units), "model_reads": model_reads,
                    "created": [{"title": u.display_title, "price_etb": u.price_etb, "area": u.sub_city} for u in units], "skipped": skipped,
                    "counts": {"created": len(units), "price_changed": 0, "removed": 0, "unchanged": 0, "skipped": len(skipped)}, "notes": []}
        report = await self.apply(developer, units, source="portal", full_sync=False, dry_run=dry_run, items_found=len(items), skipped=skipped)
        report["model_reads"] = model_reads
        return report

    # ---------------- applying units ----------------

    async def apply(self, developer: dict, units: list[ListingUnit], *, source: str, full_sync: bool, dry_run: bool,
                    floor_plan: tuple[bytes, str] | None = None, items_found: int | None = None, skipped: list[dict] | None = None) -> dict:
        report: dict[str, Any] = {
            "developer": developer["slug"], "source": source, "dry_run": dry_run, "full_sync": full_sync, "found": len(units),
            "created": [], "price_changed": [], "image_updated": [], "reactivated": [], "removed": [], "skipped": list(skipped or []),
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
                                                "new_price_etb": unit.price_etb, "change_pct": _pct(old_price, unit.price_etb),
                                                "kind": "PRICE_INCREASE" if unit.price_etb > old_price else "PRICE_DROP"})
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

        if floor_plan is not None:
            if len(new_units) == 1:
                report["floor_plan_for"] = new_units[0][0].display_title
            else:
                floor_plan = None
                report["notes"].append("The floor plan was not used: it can only be attached when the text announces exactly one new unit.")
        if new_units and not dry_run:
            await self._create(developer, new_units, source, report, floor_plan)
        if full_sync:
            await self._sync_removed(developer, seen, report, dry_run)

        counts = {"created": len(report["created"]), "price_changed": len(report["price_changed"]), "removed": len(report["removed"]),
                  "unchanged": report["unchanged"], "skipped": len(report["skipped"])}
        report["counts"] = counts
        if not dry_run:
            report["run_id"] = await self.repo.record_ingest_run(developer_id=developer["id"], source=source, dry_run=False, full_sync=full_sync, counts=counts, report=report, items_found=items_found)
            if self.events:
                self.events.publish("ingest", developer=developer["slug"], source=source, **counts)
        return report

    async def _create(self, developer: dict, new_units: list[tuple[ListingUnit, str]], source: str, report: dict,
                      floor_plan: tuple[bytes, str] | None = None) -> None:
        tags: dict[str, FloorPlanTags] = {}
        vision_budget = self.settings.ingest_max_vision_per_run
        for unit, digest in new_units:
            if floor_plan is not None:  # an uploaded plan always belongs to the single new unit
                try:
                    tags[digest] = await self.gateway.describe_image(floor_plan[0], mime_type=floor_plan[1], prompt=VISION_PROMPT, schema=FloorPlanTags)
                except Exception as exc:
                    log.warning("uploaded floor plan could not be read: %s", type(exc).__name__)
                    report["notes"].append(f"Could not read the uploaded floor plan ({type(exc).__name__}).")
                continue
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
            amenities = {"tags": plan.tags if plan else [], "source": {"text": "announcement", "portal": "portal"}.get(source, "scrape"),
                         "ingested_at": datetime.now(UTC).isoformat()}
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
