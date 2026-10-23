"""HTTP layer for the proactive jobs: who may trigger them, and the demo flow from a price change to an alert."""
import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from tests.agent_helpers import FakeGateway, make_chat, make_proactive
from tests.test_proactive import NOAH, make_buyer, pid

pytestmark = pytest.mark.usefixtures("quiet_world")

ADMIN = {"X-Admin-Key": "test-admin-key"}
CRON = {"X-Cron-Secret": "test-cron-secret"}
OVERRIDES = dict(admin_api_key="test-admin-key", cron_secret="test-cron-secret")


async def client_for(repo, **overrides):
    chat, services, agent = make_chat(repo, FakeGateway(), **{**OVERRIDES, **overrides})
    proactive, _, _ = make_proactive(repo, services=services, **{**OVERRIDES, **overrides})
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.proactive = chat.settings, repo, chat, proactive
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, services


@pytest_asyncio.fixture
async def api(spy_repo):
    client, services = await client_for(spy_repo)
    async with client:
        yield client, services


JOBS = ["/api/cron/daily-proactive-run", "/api/cron/reverse-rag-alerts", "/api/cron/tour-reminders",
        "/api/cron/milestone-reminders", "/api/cron/reminders"]


@pytest.mark.parametrize("path", JOBS)
async def test_jobs_accept_the_cron_secret_or_the_admin_key_and_nothing_else(api, path):
    client, _ = api
    assert (await client.post(path)).status_code == 401
    assert (await client.post(path, headers={"X-Cron-Secret": "wrong"})).status_code == 401
    assert (await client.post(path, headers={"X-Admin-Key": "wrong"})).status_code == 401
    assert (await client.post(path, headers=CRON)).status_code == 200
    assert (await client.post(path, headers=ADMIN)).status_code == 200


async def test_jobs_are_closed_when_no_credentials_are_configured(spy_repo):
    client, _ = await client_for(spy_repo, cron_secret="", admin_api_key="")
    async with client:
        assert (await client.post("/api/cron/daily-proactive-run")).status_code == 503
        assert (await client.post("/api/cron/daily-proactive-run", headers={"X-Cron-Secret": ""})).status_code == 503


async def test_an_empty_cron_secret_never_matches_an_empty_header(spy_repo):
    client, _ = await client_for(spy_repo, cron_secret="")
    async with client:
        assert (await client.post("/api/cron/daily-proactive-run", headers={"X-Cron-Secret": ""})).status_code == 401
        assert (await client.post("/api/cron/daily-proactive-run", headers=ADMIN)).status_code == 200


async def test_the_daily_run_reports_every_job(api):
    client, _ = api
    body = (await client.post("/api/cron/daily-proactive-run", params={"dry_run": "true"}, headers=CRON)).json()
    assert body["dry_run"] is True and [j["job"] for j in body["jobs"]] == ["reverse_rag_alerts", "tour_reminders", "milestone_reminders"]
    combined = (await client.post("/api/cron/reminders", headers=ADMIN)).json()
    assert [j["job"] for j in combined["jobs"]] == ["tour_reminders", "milestone_reminders"]


async def test_demo_flow_a_price_change_leads_to_an_alert_and_a_feed_entry(api, spy_repo, db_conn):
    client, services = api
    abel = await make_buyer(spy_repo, db_conn, "demo")
    prop = str(await pid(db_conn, NOAH))

    changed = await client.post(f"/api/properties/{prop}/price", json={"price_etb": 17_500_000}, headers=ADMIN)
    assert changed.status_code == 200 and changed.json()["change_pct"] == pytest.approx(-11.62) and changed.json()["changed"]

    preview = (await client.post("/api/cron/reverse-rag-alerts", params={"dry_run": "true"}, headers=ADMIN)).json()
    assert preview["sent"] == 0 and services.telegram.messages == []

    run = (await client.post("/api/cron/reverse-rag-alerts", headers=CRON)).json()
    assert run["sent"] == 1 and [m["chat_id"] for m in services.telegram.messages] == [abel["telegram_id"]]

    feed = (await client.get("/api/proactive/events", headers=ADMIN)).json()
    assert [(e["kind"], e["audience"], e["status"]) for e in feed] == [("PRICE_DROP", "buyer", "sent")]
    assert feed[0]["buyer_name"] == "Abel Tesfaye" and "dropped from" in feed[0]["message"]
    assert (await client.get("/api/proactive/events", params={"audience": "broker"}, headers=ADMIN)).json() == []


async def test_event_feed_and_price_endpoint_need_the_admin_key(api):
    client, _ = api
    ghost = "00000000-0000-0000-0000-000000000000"
    assert (await client.get("/api/proactive/events")).status_code == 401
    assert (await client.get("/api/proactive/events", headers=CRON)).status_code == 401, "the cron secret must not read the feed"
    assert (await client.post(f"/api/properties/{ghost}/price", json={"price_etb": 1}, headers=CRON)).status_code == 401
    assert (await client.get("/api/proactive/events", params={"audience": "everyone"}, headers=ADMIN)).status_code == 422


@pytest.mark.parametrize("body,status", [({"price_etb": 0}, 422), ({"price_etb": -5}, 422), ({"price_etb": "free"}, 422),
                                         ({}, 422), ({"price_etb": 1e13}, 422)])
async def test_price_endpoint_rejects_nonsense(api, body, status):
    client, _ = api
    assert (await client.post("/api/properties/00000000-0000-0000-0000-000000000000/price", json=body, headers=ADMIN)).status_code == status


async def test_price_endpoint_unknown_property_and_bad_id(api):
    client, _ = api
    assert (await client.post("/api/properties/00000000-0000-0000-0000-000000000000/price", json={"price_etb": 5}, headers=ADMIN)).status_code == 404
    assert (await client.post("/api/properties/not-a-uuid/price", json={"price_etb": 5}, headers=ADMIN)).status_code == 422
