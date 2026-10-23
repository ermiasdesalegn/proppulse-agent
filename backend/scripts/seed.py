"""Seed 3 partner developers and 15 representative properties. Safe to run repeatedly."""
import json
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.demo_data import DEVELOPERS, P, embedding_text
from app.embeddings import embed_documents
from app.ingest.text import content_hash


def stored_vectors(path: str, texts_by_hash: dict[str, str]) -> dict[str, list[float]]:
    """Embeddings exported earlier by scripts.export_embeddings, so a local database needs no model quota."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [h for h in texts_by_hash if h not in data]
    if missing:
        raise SystemExit(f"{len(missing)} properties have no stored embedding; run scripts.export_embeddings against a seeded database")
    return {h: data[h] for h in texts_by_hash}


def main(url: str | None = None, embeddings_file: str | None = None) -> None:
    with psycopg.connect(url or get_settings().database_url) as conn:
        for d in DEVELOPERS:
            conn.execute(
                "insert into developers (name, slug, sales_email, commission_rate_pct, reliability_score, payment_plan) "
                "values (%(name)s, %(slug)s, %(sales_email)s, %(commission_rate_pct)s, %(reliability_score)s, %(payment_plan)s) "
                "on conflict (slug) do update set payment_plan = coalesce(developers.payment_plan, excluded.payment_plan)",
                {**d, "payment_plan": Jsonb(d["payment_plan"])},
            )
        dev_ids = dict(conn.execute("select slug, id from developers").fetchall())
        existing = {r[0] for r in conn.execute("select content_hash from properties")}

        todo = [(row, content_hash(row[0], row[1], row[2], row[7])) for row in P]
        todo = [(row, h) for row, h in todo if h not in existing]
        if not todo:
            print("nothing to insert; 15 properties already present")
            return

        if embeddings_file:
            stored = stored_vectors(embeddings_file, {h: embedding_text(row) for row, h in todo})
            vectors = [stored[h] for _, h in todo]
        else:
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
    main(embeddings_file=sys.argv[1] if len(sys.argv) > 1 else None)
