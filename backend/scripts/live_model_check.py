"""Read-only check of the three model features added for ingestion and ads, against the real Gemini API.
Writes nothing to the database. Needs Pillow only for the synthetic floor plan (pip install pillow).

    .venv\\Scripts\\python -m scripts.live_model_check
"""
import asyncio
import io
import sys
import time

from app.ads import AdService
from app.config import get_settings
from app.db import make_pool
from app.gemini import GeminiGateway, GenAIBackend
from app.ingest.fetch import HttpFetcher
from app.ingest.models import FloorPlanTags
from app.ingest.service import VISION_PROMPT, IngestionService
from app.repository import Repository
from app.services import build_services


def draw_floor_plan() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    rooms = {"OPEN KITCHEN": (40, 40, 380, 260), "LIVING / DINING": (380, 40, 860, 300), "MASTER BEDROOM": (40, 260, 300, 560),
             "BEDROOM 2": (300, 300, 560, 560), "MAID ROOM + BATH": (560, 300, 720, 560), "CORNER BALCONY": (720, 300, 860, 560)}
    for name, box in rooms.items():
        d.rectangle(box, outline="black", width=4)
        d.text((box[0] + 12, box[1] + 12), name, fill="black")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


async def main() -> int:
    s = get_settings()
    gateway = GeminiGateway(GenAIBackend(s.gemini_api_key, s.gemini_model, s.gemini_embed_model, fallback_models=s.fallback_models,
                                         primary_timeout=s.gemini_primary_timeout_seconds, thinking_budget=s.gemini_thinking_budget),
                            rpm=s.gemini_rpm, request_timeout=s.gemini_timeout_seconds, embed_rpm=s.gemini_embed_rpm)
    pool = make_pool(s)
    await pool.open(wait=True, timeout=20)
    repo = Repository(pool)
    ok = True
    try:
        t = time.time()
        vectors = await gateway.embed_documents(["Ayat Lake View - 3BR Apartment in Ayat, Addis Ababa.", "Studio in Kazanchis."])
        print(f"1. embed_documents: {len(vectors)} vectors of {len(vectors[0])} dimensions ({time.time() - t:.1f}s)")
        ok &= len(vectors) == 2 and len(vectors[0]) == 768

        t = time.time()
        service = IngestionService(repo, gateway, HttpFetcher(), s)
        units, rejected = await service.extract_with_model(
            "Ayat Real Estate is proud to announce Lake View Residences in Ayat. Three-bedroom apartments of 130 sqm are priced at ETB 11.9 million "
            "with a 25% down payment, delivery Q3 2027. Also available: 2BR units of 92 sqm at 8.4M birr.", "Ayat Real Estate")
        print(f"2. announcement -> {len(units)} units ({time.time() - t:.1f}s)")
        for u in units:
            print(f"     {u.display_title}: {u.sub_city}, {u.bedrooms} BR, {u.sqm:g} sqm, {u.price_etb:,.0f} ETB, down {u.down_payment_pct}, delivery {u.delivery_date}")
        ok &= len(units) == 2 and {int(u.price_etb) for u in units} == {11_900_000, 8_400_000}

        try:
            t = time.time()
            plan = await gateway.describe_image(draw_floor_plan(), mime_type="image/png", prompt=VISION_PROMPT, schema=FloorPlanTags)
            print(f"3. floor plan -> tags {plan.tags} | ratio {plan.net_to_gross_ratio} ({time.time() - t:.1f}s)")
            ok &= len(plan.tags) >= 3
        except ImportError:
            print("3. floor plan: skipped (pip install pillow to draw the test plan)")

        t = time.time()
        row = (await repo.get_properties([(await repo.list_properties_for_map())[0]["id"]]))[0]
        services = build_services(s)
        ad = await AdService(repo, gateway, services, s).compose(row, kind="NEW_LISTING")
        print(f"4. ad copy ({time.time() - t:.1f}s):\n" + "\n".join("     " + line for line in ad.splitlines()))
        ok &= row["title"] in ad
    finally:
        await gateway.aclose()
        await pool.close()
    print("RESULT:", "ok" if ok else "CHECK THE OUTPUT ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
