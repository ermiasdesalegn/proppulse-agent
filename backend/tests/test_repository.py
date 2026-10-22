from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg import errors as pgerrors

from app.repository import LedgerEntry

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


async def vec_of(db_conn, title_like: str) -> list[float]:
    cur = await db_conn.execute("select embedding::text as e from properties where title like %s", (title_like,))
    return [float(x) for x in (await cur.fetchone())["e"].strip("[]").split(",")]


async def prop_ids(db_conn, *titles: str) -> list:
    out = []
    for t in titles:
        cur = await db_conn.execute("select id from properties where title like %s", (t,))
        out.append((await cur.fetchone())["id"])
    return out


# ---------------- search & lookup ----------------

async def test_search_applies_filters_and_returns_typed_matches(repo, db_conn):
    v = await vec_of(db_conn, "Noah Bole Towers 3BR%")
    top = await repo.search_properties(v, max_budget_etb=None, min_bedrooms=None, sub_cities=None, threshold=0.99)
    assert [m.title for m in top] == ["Noah Bole Towers 3BR Apartment"] and top[0].similarity > 0.999

    cheap = await repo.search_properties(v, max_budget_etb=8_000_000, min_bedrooms=None, sub_cities=None, threshold=-1, count=20)
    assert cheap and all(m.price_etb <= 8_000_000 for m in cheap)

    cmc = await repo.search_properties(v, max_budget_etb=None, min_bedrooms=3, sub_cities=["CMC"], threshold=-1, count=20)
    assert cmc and all(m.sub_city == "CMC" and m.bedrooms >= 3 for m in cmc)

    anywhere = await repo.search_properties(v, max_budget_etb=None, min_bedrooms=None, sub_cities=[], threshold=-1, count=50)
    assert len(anywhere) == 15


async def test_get_properties_joins_developer_and_keeps_requested_order(repo, db_conn):
    a, b = await prop_ids(db_conn, "Noah Bole Penthouse%", "Ayat Garden%")
    rows = await repo.get_properties([a, b])
    assert [r["developer_slug"] for r in rows] == ["noah", "ayat"]
    assert rows[0]["sales_email"].endswith(".example") and "embedding" not in rows[0]
    assert await repo.get_properties(["00000000-0000-0000-0000-000000000000"]) == []


async def test_get_developer_by_slug(repo):
    assert (await repo.get_developer_by_slug("gift"))["name"] == "Gift Real Estate"
    assert await repo.get_developer_by_slug("nobody") is None


# ---------------- buyers ----------------

async def test_get_or_create_buyer_is_idempotent(repo):
    a = await repo.get_or_create_buyer("tg:111", telegram_id=111)
    b = await repo.get_or_create_buyer("tg:111", telegram_id=111)
    assert a["id"] == b["id"] and a["deal_stage"] == "NEW" and a["human_takeover"] is False
    web = await repo.get_or_create_buyer("web:abc")
    assert web["telegram_id"] is None and web["id"] != a["id"]


async def test_update_buyer_only_allows_known_fields(repo):
    buyer = await repo.get_or_create_buyer("tg:222", telegram_id=222)
    updated = await repo.update_buyer(
        buyer["id"], full_name="Abel", max_budget_etb=14_000_000, preferred_sub_cities=["Bole", "CMC"], deal_stage="QUALIFIED"
    )
    assert updated["full_name"] == "Abel" and updated["max_budget_etb"] == Decimal("14000000") and updated["preferred_sub_cities"] == ["Bole", "CMC"]
    with pytest.raises(ValueError):
        await repo.update_buyer(buyer["id"], human_takeover=True)  # has its own method
    with pytest.raises(ValueError):
        await repo.update_buyer(buyer["id"], id="x")
    with pytest.raises(LookupError):
        await repo.update_buyer("00000000-0000-0000-0000-000000000000", full_name="x")


async def test_takeover_flag_toggles(repo):
    buyer = await repo.get_or_create_buyer("tg:333", telegram_id=333)
    assert (await repo.set_human_takeover(buyer["id"], True))["human_takeover"] is True
    assert (await repo.set_human_takeover(buyer["id"], False))["human_takeover"] is False


async def test_preference_embedding_roundtrip_is_searchable(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:444", telegram_id=444)
    vec = await vec_of(db_conn, "Ayat Garden%")
    await repo.set_preference_embedding(buyer["id"], "14M ETB, 3BR, garden", vec)
    cur = await db_conn.execute(
        "select preference_summary, vector_dims(preference_embedding) as d, "
        "1 - (preference_embedding <=> (select embedding from properties where title like 'Ayat Garden%%')) as sim "
        "from buyers_crm where id = %s", (buyer["id"],))
    row = await cur.fetchone()
    assert row["preference_summary"] == "14M ETB, 3BR, garden" and row["d"] == 768 and row["sim"] > 0.999


# ---------------- tours ----------------

async def test_create_tour_stores_stops_in_order_and_route_json(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:555", telegram_id=555)
    p = await prop_ids(db_conn, "Gift Bole Court%", "Noah Kazanchis%", "Ayat Garden%")
    route = {"geometry": "abc", "distance_meters": 15400, "duration_seconds": 3200, "bbox": [38.7, 8.9, 38.8, 9.0]}
    tour = await repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW, stops=p, route_json=route, calendar_event_id="evt1")
    assert tour["mapbox_route_json"] == route and tour["reminder_sent"] is False
    stops = await repo.get_tour_stops(tour["id"])
    assert [s["property_id"] for s in stops] == p and [s["stop_order"] for s in stops] == [1, 2, 3]


async def test_tour_window_query_and_reminder_flag(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:666", telegram_id=666)
    (p,) = await prop_ids(db_conn, "Ayat Garden%")
    soon = await repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW + timedelta(hours=20), stops=[p], route_json=None, calendar_event_id=None)
    await repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW + timedelta(days=5), stops=[p], route_json=None, calendar_event_id=None)
    found = await repo.tours_starting_between(NOW, NOW + timedelta(hours=24))
    assert [t["id"] for t in found] == [soon["id"]] and found[0]["telegram_id"] == 666
    await repo.mark_reminder_sent(soon["id"])
    assert (await repo.tours_starting_between(NOW, NOW + timedelta(hours=24)))[0]["reminder_sent"] is True


async def test_failed_tour_creation_leaves_nothing_behind(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:777", telegram_id=777)
    (p,) = await prop_ids(db_conn, "Ayat Garden%")
    with pytest.raises(pgerrors.ForeignKeyViolation):
        await repo.create_tour(
            buyer_id=buyer["id"], scheduled_start=NOW, stops=[p, "00000000-0000-0000-0000-000000000000"],
            route_json=None, calendar_event_id=None,
        )
    cur = await db_conn.execute("select count(*) as n from tours_and_appointments where buyer_id = %s", (buyer["id"],))
    assert (await cur.fetchone())["n"] == 0


# ---------------- ledger ----------------

async def test_ledger_rows_derive_developer_and_commission_from_the_property(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:888", telegram_id=888)
    ayat, noah, gift = await prop_ids(db_conn, "Ayat Garden%", "Noah Bole Towers 3BR%", "Gift Bole Court%")
    tour = await repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW, stops=[ayat, noah, gift], route_json=None, calendar_event_id=None)
    rows = await repo.create_ledger_rows(
        buyer_id=buyer["id"], tour_id=tour["id"],
        entries=[LedgerEntry(ayat, "em_1"), LedgerEntry(noah, "em_2"), LedgerEntry(gift)],
    )
    assert len(rows) == 3 and all(r["status"] == "LEAD_REGISTERED" for r in rows)
    # price x commission rate: Ayat 14.5M x 3% , Noah 19.8M x 2.5% , Gift 17.3M x 4%
    assert [r["expected_commission_etb"] for r in rows] == [Decimal("435000.00"), Decimal("495000.00"), Decimal("692000.00")]
    assert rows[0]["lead_registered_at"] is not None and rows[2]["lead_registered_at"] is None
    assert len({r["developer_id"] for r in rows}) == 3


async def test_ledger_status_flow_and_validation(repo, db_conn):
    buyer = await repo.get_or_create_buyer("tg:999", telegram_id=999)
    ayat, noah = await prop_ids(db_conn, "Ayat Garden%", "Noah Bole Towers 3BR%")
    won, lost = await repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(noah, "e1"), LedgerEntry(ayat, "e2")])

    signed = await repo.update_ledger(won["id"], status="CONTRACT_SIGNED", contract_value_etb=19_800_000, next_buyer_milestone_date="2026-12-01")
    assert signed["status"] == "CONTRACT_SIGNED" and signed["contract_value_etb"] == Decimal("19800000")
    assert (await repo.update_ledger(lost["id"], status="CLOSED_LOST"))["status"] == "CLOSED_LOST"

    open_rows = await repo.list_ledger("CONTRACT_SIGNED")
    assert [r["id"] for r in open_rows if r["buyer_id"] == buyer["id"]] == [won["id"]]
    assert open_rows[0]["developer_name"] and open_rows[0]["property_title"]

    with pytest.raises(ValueError):
        await repo.update_ledger(won["id"], buyer_id="x")
    with pytest.raises(pgerrors.CheckViolation):
        await repo.update_ledger(won["id"], status="SOLD")


async def test_ledger_rejects_unknown_property(repo):
    buyer = await repo.get_or_create_buyer("tg:1010", telegram_id=1010)
    with pytest.raises(LookupError):
        await repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry("00000000-0000-0000-0000-000000000000")])
