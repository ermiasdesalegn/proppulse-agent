"""Demo projects for developers that publish no prices. Fictional, like the demo units: the names and details are invented for trying the flow.
Their source address starts with demo: so a reset can tell them from projects found by crawling a real site."""
from __future__ import annotations

import io
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


def placeholder_image(name: str, area: str) -> bytes:
    """A plain picture card for a demo project, so a channel post always has something to show. Real projects use their own photo or video."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1280, 720), (16, 16, 16))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1280, 8), fill=(255, 106, 19))
    try:
        big, small = ImageFont.truetype("arial.ttf", 76), ImageFont.truetype("arial.ttf", 36)
    except OSError:
        big = small = ImageFont.load_default()
    draw.text((80, 250), name, fill=(255, 255, 255), font=big)
    draw.text((80, 360), f"{area}, Addis Ababa  -  demo project", fill=(156, 163, 175), font=small)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


async def restore_demo_projects(repo: Any, embed: Any = None, *, remove_crawled: bool = True) -> dict:
    """Remove projects that were crawled, put the demo ones back without prices, and give the demo offer a fresh end date."""
    gone = [{"n": 0}]
    if remove_crawled:  # a reset removes what was crawled; seeding the demo projects alone must never delete real ones
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
        if not await repo.list_project_media(saved["id"]):
            await repo.add_project_media(saved["id"], kind="photo", mime="image/jpeg", filename="demo-placeholder.jpg", data=placeholder_image(p["name"], p["sub_city"]))
        if embed is not None:
            await embed(await repo.get_project(saved["id"]))  # a full row: the saved one is a stub when nothing changed
        restored += 1
    return {"removed_crawled_projects": gone[0]["n"], "restored_demo_projects": restored}
