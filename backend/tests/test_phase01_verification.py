"""Independent checks for Phase 0 (setup) and Phase 1 (database). Hits the real Gemini API and Supabase.

Run:  .venv\\Scripts\\python -m pytest tests/test_phase01_verification.py -v
Every write test happens inside a transaction that is rolled back.
"""
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.embeddings import DIM, embed_query
from app.main import app

REPO = Path(__file__).resolve().parents[2]
TABLES = {
    "developers", "properties", "price_history", "buyers_crm",
    "tours_and_appointments", "tour_properties", "commission_ledger",
}


@pytest.fixture
def conn():
    c = psycopg.connect(get_settings().database_url)
    yield c
    c.rollback()
    c.close()


def self_vector(conn, title_like: str) -> str:
    return conn.execute("select embedding::text from properties where title like %s", (title_like,)).fetchone()[0]


def search(conn, vec, budget=None, beds=None, cities=None, count=50, threshold=-1.0):
    return conn.execute(
        "select * from match_properties_hybrid(%s::vector, %s, %s, %s::text[], %s, %s)",
        (vec, budget, beds, cities, count, threshold),
    ).fetchall()


# ---------------- Phase 0: setup ----------------

def test_required_settings_present():
    s = get_settings()
    for name in ["gemini_api_key", "supabase_url", "supabase_service_role_key", "database_url",
                 "telegram_bot_token", "telegram_webhook_secret", "admin_api_key", "cron_secret"]:
        assert getattr(s, name), f"{name} is empty"
    assert s.gemini_rpm <= 15


def test_health_endpoint():
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_gemini_text_model_responds():
    from google import genai
    from google.genai.errors import ServerError
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

    s = get_settings()

    @retry(retry=retry_if_exception_type(ServerError), stop=stop_after_attempt(4), wait=wait_fixed(5), reraise=True)
    def call():
        return genai.Client(api_key=s.gemini_api_key).models.generate_content(model=s.gemini_model, contents="Say ok")

    assert (call().text or "").strip()


def test_embedding_has_768_dimensions():
    assert len(embed_query("two bedroom apartment in Bole")) == DIM == 768


def test_secrets_not_in_anything_git_would_commit():
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert not [f for f in files if Path(f).name.startswith(".env") and Path(f).name != ".env.example"]
    secrets = [
        line.split("=", 1)[1].strip()
        for line in (REPO / "backend" / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
        and line.split("=", 1)[0] in {"GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL",
                                       "TELEGRAM_BOT_TOKEN", "RESEND_API_KEY", "ADMIN_API_KEY"}
    ]
    for f in files:
        p = REPO / f
        if p.suffix in {".docx", ".png"} or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for sec in filter(None, secrets):
            assert sec not in text, f"secret value found in {f}"


# ---------------- Phase 1: schema ----------------

def test_all_tables_exist(conn):
    found = {r[0] for r in conn.execute("select tablename from pg_tables where schemaname='public'")}
    assert TABLES <= found


def test_tours_use_junction_table_not_array(conn):
    cols = {r[0] for r in conn.execute(
        "select column_name from information_schema.columns where table_name='tours_and_appointments'")}
    assert "property_ids" not in cols
    pk = {r[0] for r in conn.execute(
        "select a.attname from pg_index i join pg_attribute a on a.attrelid=i.indrelid and a.attnum=any(i.indkey) "
        "where i.indrelid='tour_properties'::regclass and i.indisprimary")}
    assert pk == {"tour_id", "property_id"}


def test_hnsw_index_uses_cosine_with_agreed_parameters(conn):
    d = conn.execute("select indexdef from pg_indexes where indexname='properties_embedding_hnsw'").fetchone()[0]
    assert "hnsw" in d and "vector_cosine_ops" in d and "m='16'" in d and "ef_construction='64'" in d


def test_embedding_columns_are_768_dimensional(conn):
    for table, col in [("properties", "embedding"), ("buyers_crm", "preference_embedding")]:
        t = conn.execute(
            "select format_type(atttypid, atttypmod) from pg_attribute where attrelid=%s::regclass and attname=%s",
            (table, col)).fetchone()[0]
        assert t == "vector(768)"


def test_row_level_security_enabled_everywhere(conn):
    off = [r[0] for r in conn.execute(
        "select relname from pg_class where relname = any(%s) and not relrowsecurity", (list(TABLES),))]
    assert off == []


def test_migrations_recorded_and_rerun_is_a_noop(conn):
    from scripts import migrate
    before = conn.execute("select count(*) from schema_migrations").fetchone()[0]
    assert before == 2
    migrate.main()
    assert conn.execute("select count(*) from schema_migrations").fetchone()[0] == before


def test_constraints_reject_bad_data(conn):
    dev = conn.execute("select id from developers limit 1").fetchone()[0]
    base = ("insert into properties (developer_id,title,project_name,unit_type,sub_city,price_etb,bedrooms,sqm,"
            "content_hash,status) values (%s,'t','p','u','Bole',%s,1,50,%s,%s)")
    for args in [(dev, 0, "x1", "AVAILABLE"), (dev, 100, "x2", "SOLD")]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(base, args)
    existing = conn.execute("select content_hash from properties limit 1").fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(base, (dev, 100, existing, "AVAILABLE"))
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("insert into buyers_crm (thread_id, deal_stage) values ('t:bad','NOT_A_STAGE')")


def test_deleting_a_tour_cascades_to_its_stops(conn):
    b = conn.execute("insert into buyers_crm (thread_id) values ('t:cascade') returning id").fetchone()[0]
    t = conn.execute("insert into tours_and_appointments (buyer_id, scheduled_start) values (%s, now()) returning id", (b,)).fetchone()[0]
    p = conn.execute("select id from properties limit 1").fetchone()[0]
    conn.execute("insert into tour_properties values (%s,%s,1)", (t, p))
    conn.execute("delete from tours_and_appointments where id=%s", (t,))
    assert conn.execute("select count(*) from tour_properties where tour_id=%s", (t,)).fetchone()[0] == 0


# ---------------- Phase 1: seed data ----------------

def test_seed_counts_and_integrity(conn):
    assert conn.execute("select count(*) from developers").fetchone()[0] == 3
    assert conn.execute("select count(*) from properties").fetchone()[0] == 15
    assert conn.execute("select count(*) from properties where vector_dims(embedding)=768").fetchone()[0] == 15
    assert conn.execute("select count(distinct content_hash) from properties").fetchone()[0] == 15
    assert conn.execute("select count(*) from properties p left join developers d on d.id=p.developer_id where d.id is null").fetchone()[0] == 0
    assert {r[0] for r in conn.execute("select slug from developers")} == {"ayat", "noah", "gift"}


def test_seed_spans_agreed_variety(conn):
    cities = {r[0] for r in conn.execute("select distinct sub_city from properties")}
    assert {"Bole", "CMC", "Kazanchis", "Ayat", "Sarbet"} <= cities
    lo, hi = conn.execute("select min(price_etb), max(price_etb) from properties").fetchone()
    assert lo < 5_000_000 and hi > 25_000_000


# ---------------- Phase 1: search function behaviour ----------------

def test_search_finds_itself_first_with_perfect_similarity(conn):
    v = self_vector(conn, "Noah Bole Penthouse%")
    rows = search(conn, v, count=3, threshold=0.99)
    assert len(rows) == 1 and rows[0][2] == "Noah Bole Penthouse Penthouse 4BR" and rows[0][6] > 0.999


def test_results_ordered_by_similarity_and_limited(conn):
    v = self_vector(conn, "Ayat Garden%")
    rows = search(conn, v, count=5)
    sims = [r[6] for r in rows]
    assert len(rows) == 5 and sims == sorted(sims, reverse=True)


def test_budget_filter(conn):
    rows = search(conn, self_vector(conn, "Ayat Garden%"), budget=8_000_000.0)
    assert rows and all(r[4] <= 8_000_000 for r in rows)


def test_bedroom_filter(conn):
    rows = search(conn, self_vector(conn, "Ayat Garden%"), beds=4)
    assert rows and all(r[5] >= 4 for r in rows)


def test_sub_city_array_filter(conn):
    v = self_vector(conn, "Ayat Garden%")
    assert {r[3] for r in search(conn, v, cities=["Bole"])} == {"Bole"}
    assert {r[3] for r in search(conn, v, cities=["Bole", "CMC"])} == {"Bole", "CMC"}


def test_empty_or_null_sub_city_means_no_geographic_filter(conn):
    v = self_vector(conn, "Ayat Garden%")
    everywhere = len(search(conn, v, count=100))
    assert everywhere == 15
    assert len(search(conn, v, cities=[], count=100)) == everywhere
    assert len(search(conn, v, cities=None, count=100)) == everywhere


def test_threshold_is_a_hard_floor(conn):
    v = self_vector(conn, "Ayat Garden%")
    assert all(r[6] >= 0.70 for r in search(conn, v, threshold=0.70))
    assert search(conn, v, threshold=1.01) == []


def test_unavailable_units_are_never_returned(conn):
    v = self_vector(conn, "Noah Bole Penthouse%")
    conn.execute("update properties set status='UNAVAILABLE' where title like 'Noah Bole Penthouse%'")
    assert all("Penthouse" not in r[2] for r in search(conn, v, count=100))


def test_tuned_threshold_separates_relevant_from_irrelevant_queries():
    thr = get_settings().similarity_threshold
    with psycopg.connect(get_settings().database_url) as c:
        good = search(c, str(embed_query("3 bedroom apartment in Bole with a balcony and gym")), threshold=thr)
        bad = search(c, str(embed_query("commercial warehouse with loading dock")), threshold=thr)
    assert good and not bad
