"""A real Postgres with pgvector for the test suite, with no Docker and no network.

By default an embedded Postgres (the `pgserver` package) runs from backend/.pgdata and is migrated and seeded on first use; later
runs reuse it and only apply new migrations. Set TEST_DATABASE_URL to use a database you already have instead (CI does this with a
pgvector service container). The seed uses stored embeddings, so building it costs no Gemini quota.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND / ".pgdata"
EMBEDDINGS = BACKEND / "tests" / "fixtures" / "seed_embeddings.json"
DB_NAME = "proppulse_test"

_server = None  # kept referenced so the embedded server lives as long as the test run


def _prepare(url: str) -> None:
    from scripts.migrate import migrate
    from scripts.seed import main as seed

    migrate(url)
    seed(url=url, embeddings_file=str(EMBEDDINGS))


def local_database_url() -> str:
    """URL of the database the tests run against, creating, migrating and seeding it when needed."""
    global _server
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        _prepare(explicit)
        return explicit

    import pgserver

    last: Exception | None = None
    for _ in range(2):  # a server that a previous run is still shutting down can refuse the first connection
        try:
            _server = pgserver.get_server(DATA_DIR, cleanup_mode="stop")
            admin_url = _server.get_uri()
            with psycopg.connect(admin_url, autocommit=True, connect_timeout=20) as conn:
                if not conn.execute("select 1 from pg_database where datname = %s", (DB_NAME,)).fetchone():
                    conn.execute(f'create database "{DB_NAME}"')
            break
        except Exception as exc:  # noqa: BLE001 - any start-up failure is retried once, then reported
            last, _server = exc, None
            time.sleep(3)
    else:
        raise RuntimeError(f"the embedded test database did not start: {last}")
    url = admin_url.rsplit("/", 1)[0] + f"/{DB_NAME}"
    with psycopg.connect(url, autocommit=True, connect_timeout=20) as conn:
        conn.execute("create extension if not exists vector")
    _prepare(url)
    return url
