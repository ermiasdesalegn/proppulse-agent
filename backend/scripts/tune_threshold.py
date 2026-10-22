"""Print similarity scores for sample buyer queries to choose the retrieval threshold."""
import psycopg

from app.config import get_settings
from app.embeddings import embed_query

# (query, should_match): positives describe seeded units; negatives should fall below the threshold.
QUERIES = [
    ("cozy family home with a garden near good schools", True),
    ("3 bedroom apartment in Bole with a balcony and gym", True),
    ("affordable studio to rent out as an investment", True),
    ("luxury penthouse with roof terrace and panoramic view", True),
    ("two bedroom apartment for a young family", True),
    ("villa with swimming pool and servant quarters", True),
    ("apartment close to my office in Kazanchis", True),
    ("quiet ground floor flat with a terrace", True),
    ("5 hectare farm with a barn near Bahir Dar", False),
    ("commercial warehouse with loading dock", False),
    ("beachfront hotel with 40 rooms", False),
    ("hello, how are you", False),
]


def main() -> None:
    with psycopg.connect(get_settings().database_url) as conn:
        pos, neg = [], []
        for q, should in QUERIES:
            vec = str(embed_query(q))
            rows = conn.execute(
                "select title, sub_city, similarity from match_properties_hybrid(%s::vector, null, null, null, 3, -1)",
                (vec,),
            ).fetchall()
            top = rows[0][2]
            (pos if should else neg).append(top)
            print(f"\n{'MATCH ' if should else 'REJECT'} | {q}")
            for t, c, s in rows:
                print(f"   {s:.3f}  {t} ({c})")
        print(f"\nweakest positive top-1: {min(pos):.3f}   strongest negative top-1: {max(neg):.3f}")
        print(f"suggested threshold range: ({max(neg):.3f}, {min(pos):.3f})")

        print("\nEXPLAIN (seqscan off, to show the HNSW index is usable):")
        vec = str(embed_query(QUERIES[0][0]))
        conn.execute("set enable_seqscan = off")
        plan = conn.execute(
            "explain select id from properties order by embedding <=> %s::vector limit 3", (vec,)
        ).fetchall()
        for (line,) in plan:
            print("  ", line[:60] if "<=>" in line else line)


if __name__ == "__main__":
    main()
