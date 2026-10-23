"""Apply backend/migrations/*.sql in order, once each, each in its own transaction."""
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def migrate(url: str) -> None:
    with psycopg.connect(url, autocommit=False) as conn:
        conn.execute(
            "create table if not exists schema_migrations "
            "(name text primary key, applied_at timestamptz not null default now())"
        )
        conn.commit()
        applied = {r[0] for r in conn.execute("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                print(f"skip   {path.name}")
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            print(f"apply  {path.name}")


def main() -> None:
    migrate(get_settings().database_url)


if __name__ == "__main__":
    main()
