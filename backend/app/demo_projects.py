"""Demo projects for developers that publish no prices. Fictional, like the demo units: the names and details are invented for trying the flow.
Their source address starts with demo: so a reset can tell them from projects found by crawling a real site."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

DEMO_PREFIX = "demo:"

DEMO_PROJECTS = [
    dict(dev="ayat", name="Ayat Sky Residences", sub_city="CMC", location_text="A quiet residential block close to schools and a park", bedrooms_min=2, bedrooms_max=4,
         sqm_min=96, sqm_max=210, floors=14, summary="Family apartments with generous balconies and flexible payment options.", offer=None),
    dict(dev="noah", name="Noah Park Terraces", sub_city="Kazanchis", location_text="Walking distance to offices, hotels and the UN area", bedrooms_min=1, bedrooms_max=3,
         sqm_min=62, sqm_max=140, floors=18, summary="Mid-rise apartments in the business district, popular with investors.",
         offer=("Reserve this month and get 5% off the first instalment", 5, 30)),
    dict(dev="gift", name="Gift Garden Court", sub_city="Sarbet", location_text="A green compound near the ring road", bedrooms_min=2, bedrooms_max=3,
         sqm_min=105, sqm_max=150, floors=8, summary="Low-rise family homes around a shared garden.", offer=None),
]


async def restore_demo_projects(repo: Any, embed: Any = None) -> dict:
    """Remove projects that were crawled, put the demo ones back without prices, and give the demo offer a fresh end date."""
    gone = await repo._fetch("with gone as (delete from projects where source_url not like %s returning 1) select count(*)::int as n from gone", (DEMO_PREFIX + "%",))
    developers = {d["slug"]: d["id"] for d in await repo.list_developers()}
    restored = 0
    for p in DEMO_PROJECTS:
        if p["dev"] not in developers:
            continue
        slug = p["name"].lower().replace(" ", "-")
        offer = p["offer"]
        row = {
            "name": p["name"], "slug": slug, "source_url": f"{DEMO_PREFIX}{slug}", "sub_city": p["sub_city"], "location_text": p["location_text"], "summary": p["summary"],
            "bedrooms_min": p["bedrooms_min"], "bedrooms_max": p["bedrooms_max"], "sqm_min": p["sqm_min"], "sqm_max": p["sqm_max"], "floors": p["floors"],
            "status": "SELLING", "image_url": None, "promo_text": offer[0] if offer else None, "promo_discount_pct": offer[1] if offer else None,
            "promo_until": (date.today() + timedelta(days=offer[2])) if offer else None, "content_hash": f"demo-{slug}-{date.today()}",
        }
        saved, _ = await repo.upsert_project(developers[p["dev"]], row)
        await repo.set_project_price(saved["id"], None, None)
        if embed is not None:
            await embed(await repo.get_project(saved["id"]))  # a full row: the saved one is a stub when nothing changed
        restored += 1
    return {"removed_crawled_projects": gone[0]["n"], "restored_demo_projects": restored}
