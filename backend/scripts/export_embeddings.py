"""Save the seeded properties' embeddings to a file, keyed by content_hash (read-only on the database).

    .venv\\Scripts\\python -m scripts.export_embeddings tests/fixtures/seed_embeddings.json

The local test database is seeded from this file, so building it needs no Gemini quota.
"""
import json
import sys
from pathlib import Path

import psycopg

from app.config import get_settings


def main(target: str) -> None:
    with psycopg.connect(get_settings().database_url) as conn:
        rows = conn.execute("select content_hash, embedding::text from properties where vision_amenities->>'source' = 'seed' order by content_hash").fetchall()
    data = {h: [round(float(x), 6) for x in e.strip("[]").split(",")] for h, e in rows}
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {len(data)} embeddings to {target}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/seed_embeddings.json")
