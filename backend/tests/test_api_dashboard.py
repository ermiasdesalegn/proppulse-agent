"""Endpoints that feed the Command Center: board, deal moves, map data, tours, stats and voice-note audio."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.main import create_app
from app.repository import LedgerEntry
from app.services.telegram import MockTelegramService, TelegramError
from tests.agent_helpers import FakeGateway, make_chat, make_proactive, make_services
from tests.test_proactive import GIFT, NOAH, make_buyer, pid

pytestmark = pytest.mark.usefixtures("quiet_world")

ADMIN = {"X-Admin-Key": "test-admin-key"}


async def make_client(repo, services=None):
    services = services or make_services()
    chat, services, _ = make_chat(repo, FakeGateway(), services=services, admin_api_key="test-admin-key")
    proactive, _, _ = make_proactive(repo, services=services, admin_api_key="test-admin-key")
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.proactive = chat.settings, repo, chat, proactive
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def deal(repo, db_conn, buyer, like, status="LEAD_REGISTERED"):
    (row,) = await repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(await pid(db_conn, like), "em")])
    return row if status == "LEAD_REGISTERED" else await repo.update_ledger(row["id"], status=status)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/board"), ("PATCH", "/api/ledger/00000000-0000-0000-0000-000000000000"), ("GET", "/api/properties"),
    ("GET", "/api/tours"), ("GET", "/api/stats"), ("GET", "/api/messages/1/audio"),
])
async def test_dashboard_endpoints_need_the_admin_key(spy_repo, method, path):
    async with await make_client(spy_repo) as c:
        assert (await c.request(method, path, json={"status": "COMMISSION_PAID"})).status_code == 401
        assert (await c.request(method, path, json={}, headers={"X-Admin-Key": "wrong"})).status_code == 401


# ---------------- board ----------------

async def test_board_lists_qualified_buyers_without_a_deal_and_every_deal(spy_repo, db_conn):
    ready = await make_buyer(spy_repo, db_conn, "ready", name="Ready Buyer")
    waiting = await make_buyer(spy_repo, db_conn, "waiting", stage="INVENTORY_PENDING", name="Waiting Buyer")
    fresh = await make_buyer(spy_repo, db_conn, "fresh", stage="NEW", name="Fresh")
    has_deal = await make_buyer(spy_repo, db_conn, "deal", name="Deal Buyer")
    await deal(spy_repo, db_conn, has_deal, NOAH)

    async with await make_client(spy_repo) as c:
        body = (await c.get("/api/board", headers=ADMIN)).json()
    assert {b["full_name"] for b in body["qualified"]} == {"Ready Buyer", "Waiting Buyer"}, "NEW buyers and buyers with a deal are not in column one"
    assert [(d["buyer_name"], d["developer_name"], d["status"]) for d in body["deals"]] == [("Deal Buyer", "Noah Real Estate", "LEAD_REGISTERED")]
    assert body["deals"][0]["expected_commission_etb"] == 495000 and "preference_embedding" not in str(body)


# ---------------- moving deals ----------------

async def test_moving_a_deal_forward_carries_the_buyers_stage(spy_repo, db_conn):
    abel = await make_buyer(spy_repo, db_conn, "abel")
    row = await deal(spy_repo, db_conn, abel, NOAH)
    async with await make_client(spy_repo) as c:
        done = await c.patch(f"/api/ledger/{row['id']}", json={"status": "TOUR_COMPLETED"}, headers=ADMIN)
        assert done.status_code == 200 and done.json()["status"] == "TOUR_COMPLETED"
        assert (await spy_repo.get_buyer(abel["id"]))["deal_stage"] == "TOUR_COMPLETED"

        signed = await c.patch(f"/api/ledger/{row['id']}", json={"status": "CONTRACT_SIGNED", "contract_value_etb": 19_800_000,
                                                                 "next_buyer_milestone_date": "2026-12-15"}, headers=ADMIN)
        assert signed.status_code == 200 and signed.json()["next_buyer_milestone_date"] == "2026-12-15"
        assert float(signed.json()["contract_value_etb"]) == 19_800_000
        assert (await spy_repo.get_buyer(abel["id"]))["deal_stage"] == "CONTRACT_SIGNED"

        for status in ("COMMISSION_PENDING", "COMMISSION_PAID"):
            assert (await c.patch(f"/api/ledger/{row['id']}", json={"status": status}, headers=ADMIN)).status_code == 200
        assert (await spy_repo.get_buyer(abel["id"]))["deal_stage"] == "COMMISSION_PAID"


async def test_signing_requires_a_payment_milestone_date(spy_repo, db_conn):
    row = await deal(spy_repo, db_conn, await make_buyer(spy_repo, db_conn, "nodate"), NOAH)
    async with await make_client(spy_repo) as c:
        refused = await c.patch(f"/api/ledger/{row['id']}", json={"status": "CONTRACT_SIGNED"}, headers=ADMIN)
        assert refused.status_code == 422 and "milestone" in refused.json()["detail"]
        assert (await spy_repo.list_ledger("LEAD_REGISTERED"))[0]["status"] == "LEAD_REGISTERED", "a refused move changes nothing"


async def test_an_existing_milestone_date_is_kept_when_moving_again(spy_repo, db_conn):
    row = await deal(spy_repo, db_conn, await make_buyer(spy_repo, db_conn, "keep"), NOAH)
    async with await make_client(spy_repo) as c:
        await c.patch(f"/api/ledger/{row['id']}", json={"status": "CONTRACT_SIGNED", "next_buyer_milestone_date": "2026-12-15"}, headers=ADMIN)
        moved = await c.patch(f"/api/ledger/{row['id']}", json={"status": "COMMISSION_PENDING"}, headers=ADMIN)
        assert moved.json()["next_buyer_milestone_date"] == "2026-12-15"


async def test_losing_one_deal_does_not_change_the_buyers_stage(spy_repo, db_conn):
    abel = await make_buyer(spy_repo, db_conn, "lost")
    won, lost = await deal(spy_repo, db_conn, abel, NOAH), await deal(spy_repo, db_conn, abel, GIFT)
    async with await make_client(spy_repo) as c:
        await c.patch(f"/api/ledger/{won['id']}", json={"status": "TOUR_COMPLETED"}, headers=ADMIN)
        assert (await c.patch(f"/api/ledger/{lost['id']}", json={"status": "CLOSED_LOST"}, headers=ADMIN)).status_code == 200
    assert (await spy_repo.get_buyer(abel["id"]))["deal_stage"] == "TOUR_COMPLETED"


@pytest.mark.parametrize("body,status", [({"status": "SOLD"}, 422), ({"status": ""}, 422), ({}, 422),
                                         ({"status": "CONTRACT_SIGNED", "contract_value_etb": -1}, 422),
                                         ({"status": "CONTRACT_SIGNED", "next_buyer_milestone_date": "tomorrow"}, 422)])
async def test_bad_deal_moves_are_rejected(spy_repo, db_conn, body, status):
    row = await deal(spy_repo, db_conn, await make_buyer(spy_repo, db_conn, "bad"), NOAH)
    async with await make_client(spy_repo) as c:
        assert (await c.patch(f"/api/ledger/{row['id']}", json=body, headers=ADMIN)).status_code == status


async def test_moving_an_unknown_deal(spy_repo):
    async with await make_client(spy_repo) as c:
        assert (await c.patch("/api/ledger/00000000-0000-0000-0000-000000000000", json={"status": "COMMISSION_PAID"}, headers=ADMIN)).status_code == 404
        assert (await c.patch("/api/ledger/not-a-uuid", json={"status": "COMMISSION_PAID"}, headers=ADMIN)).status_code == 422


# ---------------- map data, tours, stats ----------------

async def test_properties_carry_coordinates_and_never_embeddings(spy_repo):
    async with await make_client(spy_repo) as c:
        rows = (await c.get("/api/properties", headers=ADMIN)).json()
    assert len(rows) == 15 and all(r["lat"] and r["lng"] and r["developer_slug"] for r in rows)
    assert "embedding" not in rows[0] and {r["developer_slug"] for r in rows} == {"ayat", "noah", "gift"}


async def test_tours_include_ordered_stops_and_the_route(spy_repo, db_conn):
    abel = await make_buyer(spy_repo, db_conn, "tours")
    route = {"geometry": "abc", "distance_meters": 15400, "duration_seconds": 3200, "bbox": [38.7, 8.9, 38.8, 9.0]}
    await spy_repo.create_tour(buyer_id=abel["id"], scheduled_start=datetime.now(UTC) + timedelta(days=1),
                               stops=[await pid(db_conn, GIFT), await pid(db_conn, NOAH)], route_json=route, calendar_event_id="e1")
    async with await make_client(spy_repo) as c:
        (tour,) = (await c.get("/api/tours", headers=ADMIN)).json()
    assert tour["full_name"] == "Abel Tesfaye" and tour["mapbox_route_json"] == route
    assert [(s["stop_order"], s["title"]) for s in tour["stops"]] == [(1, "Gift Bole Court 3BR Apartment"), (2, "Noah Bole Towers 3BR Apartment")]
    assert all(s["lat"] and s["lng"] for s in tour["stops"])


async def test_stats_summarise_the_pipeline(spy_repo, db_conn):
    async with await make_client(spy_repo) as c:
        before = (await c.get("/api/stats", headers=ADMIN)).json()
        abel = await make_buyer(spy_repo, db_conn, "stats")
        await deal(spy_repo, db_conn, abel, NOAH)           # 495,000 expected
        await deal(spy_repo, db_conn, abel, GIFT, status="COMMISSION_PENDING")  # 692,000 expected
        await spy_repo.create_tour(buyer_id=abel["id"], scheduled_start=datetime.now(UTC) + timedelta(days=2),
                                   stops=[await pid(db_conn, NOAH)], route_json=None, calendar_event_id=None)
        after = (await c.get("/api/stats", headers=ADMIN)).json()
    assert after["buyers"] == before["buyers"] + 1 and after["qualified"] == before["qualified"] + 1
    assert after["open_deals"] == before["open_deals"] + 2 and after["upcoming_tours"] == before["upcoming_tours"] + 1
    assert float(after["pipeline_commission_etb"]) - float(before["pipeline_commission_etb"]) == 1_187_000
    assert float(after["commission_pending_etb"]) - float(before["commission_pending_etb"]) == 692_000
    assert after["high_value_threshold_etb"] == 20_000_000
    buyers_trend, pipeline_trend = after["trends"]["buyers"], after["trends"]["pipeline_commission_etb"]
    assert buyers_trend["current"] == before["trends"]["buyers"]["current"] + 1
    assert pipeline_trend["current"] - before["trends"]["pipeline_commission_etb"]["current"] == 1_187_000
    assert set(after["trends"]) == {"buyers", "open_deals", "pipeline_commission_etb", "upcoming_tours", "proactive_sent"}


async def test_trends_count_the_previous_period_separately(spy_repo, db_conn):
    abel = await make_buyer(spy_repo, db_conn, "trend")
    await db_conn.execute("update buyers_crm set created_at = now() - interval '45 days' where id = %s", (abel["id"],))
    t = await spy_repo.trends(30)
    assert t["buyers"]["previous"] >= 1, "a buyer created 45 days ago belongs to the previous 30-day period"


# ---------------- voice notes ----------------

async def test_voice_note_audio_is_served_from_telegram(spy_repo, db_conn):
    abel = await make_buyer(spy_repo, db_conn, "voice")
    voice = await spy_repo.add_message(abel["id"], "buyer", "hello", source="voice", telegram_file_id="FILE1")
    async with await make_client(spy_repo, make_services(files={"FILE1": b"OggS-audio-bytes"})) as c:
        res = await c.get(f"/api/messages/{voice['id']}/audio", headers=ADMIN)
    assert res.status_code == 200 and res.content == b"OggS-audio-bytes" and res.headers["content-type"] == "audio/ogg"
    assert "private" in res.headers["cache-control"]


async def test_audio_for_text_messages_unknown_ids_and_telegram_failures(spy_repo, db_conn):
    class Down(MockTelegramService):
        async def download_file(self, file_id):
            raise TelegramError("file expired")

    abel = await make_buyer(spy_repo, db_conn, "audio2")
    text = await spy_repo.add_message(abel["id"], "buyer", "hi")
    voice = await spy_repo.add_message(abel["id"], "buyer", "hello", source="voice", telegram_file_id="GONE")
    services = make_services()
    services.telegram = Down()
    async with await make_client(spy_repo, services) as c:
        assert (await c.get(f"/api/messages/{text['id']}/audio", headers=ADMIN)).status_code == 404
        assert (await c.get("/api/messages/999999999/audio", headers=ADMIN)).status_code == 404
        failed = await c.get(f"/api/messages/{voice['id']}/audio", headers=ADMIN)
    assert failed.status_code == 502 and "expired" not in failed.text
