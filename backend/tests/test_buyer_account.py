"""Buyer account basics (migration 039): My visits with Cancel, and the alerts switch. Real database (rolled back), mock calendar and Telegram."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from app.telegram_updates import TelegramUpdateHandler
from tests.agent_helpers import FakeGateway, ext, make_chat, make_proactive
from tests.test_portal_api import OVERRIDES, USER_A, USER_B, auth
from tests.test_proactive import NOAH, clone_property, make_buyer, pid

pytestmark = pytest.mark.usefixtures("quiet_world")


@pytest_asyncio.fixture
async def site(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(10)]), **OVERRIDES)
    proactive, _, _ = make_proactive(spy_repo, services=services, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.proactive = chat.settings, spy_repo, chat, proactive
    app.state.telegram_handler = TelegramUpdateHandler(chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, spy_repo, services


async def book(repo, user, *, days=3, event="evt-1", services=None):
    buyer = await repo.get_or_create_buyer(f"web:{user}")
    if services is not None:
        services.calendar.events[event] = {"start": datetime.now(UTC), "end": datetime.now(UTC), "summary": "visit"}
    return await repo.create_tour(buyer_id=buyer["id"], scheduled_start=datetime.now(UTC) + timedelta(days=days), stops=[],
                                  route_json=None, calendar_event_id=event)


async def test_a_buyer_sees_only_their_own_visits(site):
    client, repo, _ = site
    mine, theirs = await book(repo, USER_A, event="a"), await book(repo, USER_B, event="b")
    got = (await client.get("/api/me/visits", headers=auth(USER_A))).json()["items"]
    assert [v["id"] for v in got] == [str(mine["id"])] and got[0]["status"] == "upcoming" and got[0]["can_cancel"]
    assert str(theirs["id"]) not in str(got)


async def test_a_buyer_with_no_record_sees_an_empty_list(site):
    client, _, _ = site
    assert (await client.get("/api/me/visits", headers=auth(USER_A))).json() == {"items": []}


async def test_cancelling_marks_it_frees_the_slot_and_tells_the_broker(site, db_conn):
    client, repo, services = site
    tour = await book(repo, USER_A, services=services)
    r = await client.post(f"/api/me/visits/{tour['id']}/cancel", headers=auth(USER_A))
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and not r.json()["can_cancel"]
    assert "evt-1" not in services.calendar.events, "the calendar slot is free again"
    note = await (await db_conn.execute("select message, audience from proactive_events where dedupe_key = %s", (f"visit-cancelled:{tour['id']}",))).fetchone()
    assert note["audience"] == "broker" and "cancelled the visit" in note["message"]
    again = await client.post(f"/api/me/visits/{tour['id']}/cancel", headers=auth(USER_A))
    assert again.status_code == 409, "a cancelled booking cannot be cancelled twice"


async def test_another_buyers_booking_cannot_be_cancelled_and_looks_missing(site):
    client, repo, services = site
    theirs = await book(repo, USER_B, services=services)
    r = await client.post(f"/api/me/visits/{theirs['id']}/cancel", headers=auth(USER_A))
    assert r.status_code == 404
    assert "evt-1" in services.calendar.events
    assert (await repo.get_tour(theirs["id"]))["cancelled_at"] is None


async def test_a_visit_in_the_past_cannot_be_cancelled(site):
    client, repo, _ = site
    old = await book(repo, USER_A, days=-1)
    listed = (await client.get("/api/me/visits", headers=auth(USER_A))).json()["items"][0]
    assert listed["status"] == "past" and not listed["can_cancel"]
    assert (await client.post(f"/api/me/visits/{old['id']}/cancel", headers=auth(USER_A))).status_code == 409


async def test_a_cancelled_visit_is_not_reminded(site):
    client, repo, _ = site
    tour = await book(repo, USER_A, days=1)
    start = datetime.now(UTC)
    assert [t["id"] for t in await repo.tours_for_reminder(start, start + timedelta(days=2))] == [tour["id"]]
    await client.post(f"/api/me/visits/{tour['id']}/cancel", headers=auth(USER_A))
    assert await repo.tours_for_reminder(start, start + timedelta(days=2)) == []


async def test_visits_need_a_sign_in(site):
    client, _, _ = site
    assert (await client.get("/api/me/visits")).status_code == 401
    assert (await client.put("/api/me/alerts", json={"enabled": False})).status_code == 401


async def test_the_alerts_switch_defaults_on_and_is_kept_per_buyer(site):
    client, repo, _ = site
    assert (await client.get("/api/me/alerts", headers=auth(USER_A))).json() == {"enabled": True}
    assert (await client.put("/api/me/alerts", json={"enabled": False}, headers=auth(USER_A))).json() == {"enabled": False}
    assert (await client.get("/api/me/alerts", headers=auth(USER_A))).json() == {"enabled": False}
    assert (await client.get("/api/me/alerts", headers=auth(USER_B))).json() == {"enabled": True}
    assert (await client.put("/api/me/alerts", json={"enabled": True}, headers=auth(USER_A))).json() == {"enabled": True}
    assert (await client.put("/api/me/alerts", json={"enabled": "maybe"}, headers=auth(USER_A))).status_code == 422


async def test_an_opted_out_buyer_gets_no_alert_while_others_still_do(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    quiet = await make_buyer(repo, db_conn, "quiet", name="Quiet")
    loud = await make_buyer(repo, db_conn, "loud", name="Loud")
    await repo.set_buyer_alerts(quiet["id"], False)
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts()
    assert out["sent"] == 1
    assert [m["chat_id"] for m in services.telegram.messages] == [loud["telegram_id"]]

    await repo.set_buyer_alerts(quiet["id"], True)  # switching back on resumes alerts, with news the buyer has not had
    await repo.update_property_price(await pid(db_conn, NOAH), 16_000_000)
    await clone_property(db_conn, NOAH, "Another unit", 15_000_000)
    again = await proactive.reverse_rag_alerts()
    assert again["sent"] >= 1
    assert quiet["telegram_id"] in [m["chat_id"] for m in services.telegram.messages]
