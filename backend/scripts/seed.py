"""Seed 3 partner developers and 15 representative properties. Safe to run repeatedly."""
import json

import psycopg

from app.config import get_settings
from app.embeddings import embed_documents
from app.ingest.text import content_hash

DEVELOPERS = [
    # Seed contacts use the reserved .example domain; real sales offices go in later.
    dict(name="Ayat Real Estate", slug="ayat", sales_email="sales@ayat.example", commission_rate_pct=3.0, reliability_score=0.80),
    dict(name="Noah Real Estate", slug="noah", sales_email="sales@noah.example", commission_rate_pct=2.5, reliability_score=0.85),
    dict(name="Gift Real Estate", slug="gift", sales_email="sales@gift.example", commission_rate_pct=4.0, reliability_score=0.75),
]

# dev, project, unit_type, sub_city, (lat, lng), price_etb, beds, sqm, down%, stage%, yield%, delivery, description, tags
P = [
    ("ayat", "Ayat Garden Residences", "3BR Townhouse", "Ayat", (9.0235, 38.8690), 14_500_000, 3, 165, 30, 55, 6.5, "2027-06-30",
     "Two-storey family townhouse with a private landscaped garden, quiet gated compound, near international schools and a park.",
     ["private garden", "gated compound", "family townhouse", "near schools", "maid room", "parking x2"]),
    ("ayat", "Ayat Heights", "2BR Apartment", "Ayat", (9.0220, 38.8665), 6_800_000, 2, 88, 25, 70, 8.0, "2026-12-15",
     "Compact two-bedroom apartment in a mid-rise block, affordable entry price for first-time buyers, shared playground.",
     ["balcony", "elevator", "shared playground", "first-time buyer", "open-plan kitchen"]),
    ("ayat", "Ayat Square Studios", "Studio", "Ayat", (9.0241, 38.8702), 3_600_000, 0, 42, 20, 80, 9.0, "2026-09-30",
     "Efficient studio unit close to the main road and public transport, aimed at investors and young professionals.",
     ["investor unit", "near public transport", "compact", "high rental demand"]),
    ("ayat", "Ayat Hill Villas", "4BR Villa", "CMC", (9.0197, 38.8010), 28_000_000, 4, 280, 35, 40, 5.5, "2028-03-31",
     "Detached four-bedroom villa with a large garden, swimming pool option, servant quarters and a two-car garage in a prestigious compound.",
     ["detached villa", "large garden", "pool option", "servant quarters", "2-car garage", "prestigious compound"]),
    ("noah", "Noah Bole Towers", "3BR Apartment", "Bole", (8.9950, 38.7860), 19_800_000, 3, 135, 30, 65, 7.0, "2027-03-31",
     "High-floor three-bedroom apartment with city views in a Bole tower, wrap-around balcony, 24-hour security and gym access.",
     ["city view", "corner wrap-around balcony", "gym", "24h security", "elevator", "high floor"]),
    ("noah", "Noah Bole Towers", "2BR Apartment", "Bole", (8.9950, 38.7860), 13_200_000, 2, 96, 30, 65, 7.5, "2027-03-31",
     "Modern two-bedroom in Bole close to the airport and embassies, ideal for expatriate tenants and short-let investors.",
     ["near airport", "expat rental", "balcony", "open-plan kitchen", "gym", "elevator"]),
    ("noah", "Noah Bole Penthouse", "Penthouse 4BR", "Bole", (8.9972, 38.7889), 42_000_000, 4, 320, 40, 50, 5.0, "2028-01-31",
     "Luxury duplex penthouse with private roof terrace, panoramic views, private elevator lobby, maid room and bath.",
     ["private roof terrace", "panoramic view", "private elevator", "maid room + bath", "luxury", "duplex"]),
    ("noah", "Noah Kazanchis Plaza", "1BR Apartment", "Kazanchis", (9.0147, 38.7636), 7_400_000, 1, 62, 25, 75, 8.5, "2026-11-30",
     "One-bedroom apartment in the Kazanchis business district, walking distance to offices, hotels and the UN area.",
     ["walkable to offices", "business district", "investor unit", "elevator", "furnished option"]),
    ("noah", "Noah Sarbet Gardens", "3BR Apartment", "Sarbet", (8.9887, 38.7332), 11_900_000, 3, 128, 30, 60, 6.8, "2027-08-31",
     "Family apartment on the ground floor with a private terrace garden, quiet residential street in Sarbet near the ring road.",
     ["terrace garden", "ground floor", "quiet street", "family friendly", "parking", "storage room"]),
    ("gift", "Gift Summit Park", "3BR Apartment", "CMC", (9.0030, 38.8240), 12_600_000, 3, 140, 28, 70, 6.5, "2027-01-31",
     "Spacious three-bedroom with separate dining area in a community with kids' play area, mini-mart and covered parking.",
     ["kids play area", "separate dining", "covered parking", "community amenities", "balcony"]),
    ("gift", "Gift Summit Park", "2BR Apartment", "CMC", (9.0030, 38.8240), 8_700_000, 2, 92, 25, 70, 7.8, "2027-01-31",
     "Two-bedroom apartment with efficient layout and good net-to-gross ratio, popular with young families.",
     ["efficient layout", "high net-to-gross ratio", "balcony", "young families", "elevator"]),
    ("gift", "Gift Kazanchis Suites", "Studio", "Kazanchis", (9.0160, 38.7650), 4_900_000, 0, 48, 20, 85, 9.2, "2026-08-31",
     "Serviced studio in central Kazanchis with strong short-let income, near restaurants and the light rail.",
     ["serviced apartment", "short-let income", "central", "near light rail", "furnished option"]),
    ("gift", "Gift Lebu Homes", "4BR Twin Villa", "Lebu", (8.9575, 38.7176), 22_500_000, 4, 230, 35, 35, 5.8, "2028-06-30",
     "Semi-detached four-bedroom twin villa with front and back garden, family room, maid quarters, in a calm green neighbourhood.",
     ["front and back garden", "family room", "maid quarters", "green neighbourhood", "twin villa", "2-car parking"]),
    ("gift", "Gift Bole Court", "3BR Apartment", "Bole", (8.9921, 38.7905), 17_300_000, 3, 130, 30, 60, 7.2, "2027-05-31",
     "Three-bedroom apartment near Bole Road cafes and restaurants, large living room, two bathrooms, backup generator.",
     ["near cafes", "large living room", "backup generator", "2 bathrooms", "elevator"]),
    ("gift", "Gift Sarbet Heights", "2BR Apartment", "Sarbet", (8.9902, 38.7345), 9_200_000, 2, 90, 25, 55, 7.4, "2027-09-30",
     "Two-bedroom apartment with a sunny corner balcony, close to Sarbet schools and markets.",
     ["corner balcony", "near schools", "sunny", "market access", "elevator"]),
]


def embedding_text(row: tuple) -> str:
    _, project, unit_type, sub_city, _, price, beds, sqm, _, _, _, _, desc, tags = row
    return (
        f"{project} - {unit_type} in {sub_city}, Addis Ababa. {beds} bedrooms, {sqm} sqm, "
        f"{price:,.0f} ETB. {desc} Features: {', '.join(tags)}."
    )


def main() -> None:
    with psycopg.connect(get_settings().database_url) as conn:
        for d in DEVELOPERS:
            conn.execute(
                "insert into developers (name, slug, sales_email, commission_rate_pct, reliability_score) "
                "values (%(name)s, %(slug)s, %(sales_email)s, %(commission_rate_pct)s, %(reliability_score)s) "
                "on conflict (slug) do nothing",
                d,
            )
        dev_ids = dict(conn.execute("select slug, id from developers").fetchall())
        existing = {r[0] for r in conn.execute("select content_hash from properties")}

        todo = [(row, content_hash(row[0], row[1], row[2], row[7])) for row in P]
        todo = [(row, h) for row, h in todo if h not in existing]
        if not todo:
            print("nothing to insert; 15 properties already present")
            return

        vectors = embed_documents([embedding_text(row) for row, _ in todo])
        for (row, h), vec in zip(todo, vectors):
            slug, project, utype, city, (lat, lng), price, beds, sqm, down, stage, yld, delivery, desc, tags = row
            conn.execute(
                "insert into properties (developer_id, title, project_name, unit_type, sub_city, lat, lng, price_etb, "
                "bedrooms, sqm, down_payment_pct, construction_stage_pct, est_rental_yield_pct, delivery_date, description, "
                "vision_amenities, content_hash, embedding) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (dev_ids[slug], f"{project} {utype}", project, utype, city, lat, lng, price, beds, sqm, down, stage, yld,
                 delivery, desc, json.dumps({"tags": tags, "source": "seed"}), h, str(vec)),
            )
        conn.commit()
        print(f"inserted {len(todo)} properties")


if __name__ == "__main__":
    main()
