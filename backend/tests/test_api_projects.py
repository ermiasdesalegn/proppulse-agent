"""The dashboard's project endpoints: list, crawl, set a price, set an offer, advertise."""
from datetime import date, timedelta

import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from app.projects import ProjectService
from tests.agent_helpers import FakeGateway, make_chat, make_proactive
from tests.jobs_helpers import run_queued, total
from tests.test_projects import Ads, Site, add_project, info, project_page

ADMIN = {"X-Admin-Key": "test-admin-key"}
CRON = {"X-Cron-Secret": "test-cron-secret"}
OVERRIDES = dict(admin_api_key="test-admin-key", cron_secret="test-cron-secret", broker_telegram_chat_id="999", telegram_channel_id="@channel")

pytestmark = pytest.mark.usefixtures("quiet_world")


@pytest_asyncio.fixture
async def api(spy_repo):
    gateway = FakeGateway([info(), info()])
    chat, services, _ = make_chat(spy_repo, gateway, **OVERRIDES)
    proactive, _, _ = make_proactive(spy_repo, services=services, **OVERRIDES)
    site = Site({"https://dev.example/": "<a href='/t/'>t</a>", "https://dev.example/t/": project_page()})
    projects = ProjectService(spy_repo, gateway, services, chat.settings, fetcher=site, proactive=proactive, ads=Ads())
    chat.projects = projects
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.proactive, app.state.projects = chat.settings, spy_repo, chat, proactive, projects
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, services


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/projects"), ("GET", "/api/price-requests"), ("POST", "/api/developers/noah/crawl-projects"),
    ("PUT", "/api/projects/00000000-0000-0000-0000-000000000000/price"), ("PUT", "/api/projects/00000000-0000-0000-0000-000000000000/promo"),
    ("POST", "/api/projects/00000000-0000-0000-0000-000000000000/ad"),
])
async def test_project_endpoints_need_the_admin_key(api, method, path):
    client, _ = api
    assert (await client.request(method, path, json={})).status_code == 401


async def test_crawl_then_list_then_set_price_and_offer_and_advertise(api, spy_repo, db_conn):
    client, services = api
    preview = (await client.post("/api/developers/noah/crawl-projects", json={"url": "https://dev.example/", "dry_run": True}, headers=ADMIN)).json()
    assert [p["name"] for p in preview["created"]] == ["Central Tower"] and (await client.get("/api/projects", headers=ADMIN)).json() == []
    saved = (await client.post("/api/developers/noah/crawl-projects", json={"url": "https://dev.example/", "dry_run": False}, headers=ADMIN)).json()
    assert saved["created"][0]["name"] == "Central Tower"
    (project,) = (await client.get("/api/projects", headers=ADMIN)).json()
    assert project["price_from_etb"] is None and project["open_requests"] == 0 and project["developer_name"] == "Noah Real Estate"

    pid = project["id"]
    assert (await client.put(f"/api/projects/{pid}/price", json={"price_from_etb": 18_000_000, "note": "from the 3 bedroom"}, headers=ADMIN)).json()["agencies"] == 0, "nobody asked yet, so no agency has anything to tell"
    assert (await client.get("/api/projects", headers=ADMIN)).json()[0]["price_from_etb"] == 18_000_000
    assert (await client.put(f"/api/projects/{pid}/price", json={"price_from_etb": -1}, headers=ADMIN)).status_code == 422

    until = (date.today() + timedelta(days=10)).isoformat()
    assert (await client.put(f"/api/projects/{pid}/promo", json={"text": "5% off this month", "discount_pct": 5, "until": until}, headers=ADMIN)).status_code == 200
    ad = (await client.post(f"/api/projects/{pid}/ad", json={"dry_run": True}, headers=ADMIN)).json()
    assert "Offer: 5% off this month" in ad["preview"] and ad["buttons"] == ["Ask the price", "Book a visit", "More about this project"] and not ad["posted"]
    assert (await client.post(f"/api/projects/{pid}/ad", json={"dry_run": False}, headers=ADMIN)).json()["posted"] is True
    assert len(services.telegram.photos) + len(services.telegram.messages) == 1
    assert (await client.put(f"/api/projects/{pid}/promo", json={"text": None}, headers=ADMIN)).status_code == 200
    assert (await client.get("/api/projects", headers=ADMIN)).json()[0]["promo_text"] is None


async def test_unknown_developer_and_project_are_404_and_a_bad_address_is_422(api):
    client, _ = api
    assert (await client.post("/api/developers/nobody/crawl-projects", json={}, headers=ADMIN)).status_code == 404
    assert (await client.post("/api/developers/noah/crawl-projects", json={"url": "ftp://x"}, headers=ADMIN)).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.put(f"/api/projects/{missing}/price", json={"price_from_etb": 5}, headers=ADMIN)).status_code == 404
    assert (await client.post(f"/api/projects/{missing}/ad", json={}, headers=ADMIN)).status_code == 404


async def test_the_daily_run_includes_the_promotion_ads(api, spy_repo, db_conn):
    client, services = api
    await add_project(spy_repo, db_conn, promotion="10% off", promotion_until=(date.today() + timedelta(days=3)).isoformat())
    body = (await client.post("/api/cron/daily-proactive-run", params={"dry_run": "true"}, headers=CRON)).json()
    assert [j["job"] for j in body["jobs"]][-1] == "promotion_ads" and body["jobs"][-1]["details"][0]["would_post"] == "10% off"
    await client.post("/api/cron/promotion-ads", headers=CRON)
    assert total(await run_queued(client)) == 1


async def test_a_developer_can_be_added_from_the_dashboard(api):
    client, _ = api
    body = {"name": "Metropolitan Real Estate", "sales_email": "sales@metropolitan.example", "website_url": "https://metropolitanaddis.com/"}
    created = (await client.post("/api/developers", json=body, headers=ADMIN)).json()
    assert created["slug"] == "metropolitan-real-estate" and created["website_url"] == "https://metropolitanaddis.com/"
    assert "metropolitan-real-estate" in [d["slug"] for d in (await client.get("/api/developers", headers=ADMIN)).json()]
    assert (await client.post("/api/developers", json=body, headers=ADMIN)).status_code == 409
    assert (await client.post("/api/developers", json={**body, "name": "Other", "sales_email": "not-an-email"}, headers=ADMIN)).status_code == 422
    assert (await client.post("/api/developers", json={**body, "name": "Third", "website_url": "javascript:alert(1)"}, headers=ADMIN)).status_code == 422
    assert (await client.post("/api/developers", json=body)).status_code == 401


async def test_media_can_be_uploaded_listed_served_and_removed_and_the_sweep_status_is_readable(api, spy_repo, db_conn):
    import base64

    client, _ = api
    project = await add_project(spy_repo, db_conn)
    pid = str(project["id"])
    body = {"filename": "front.jpg", "mime": "image/jpeg", "data_base64": base64.b64encode(b"JPEGBYTES").decode()}
    row = (await client.post(f"/api/projects/{pid}/media", json=body, headers=ADMIN)).json()
    assert row["kind"] == "photo" and row["size_bytes"] == 9
    assert [m["id"] for m in (await client.get(f"/api/projects/{pid}/media", headers=ADMIN)).json()] == [row["id"]]
    served = await client.get(f"/api/projects/{pid}/media/{row['id']}", headers=ADMIN)
    assert served.content == b"JPEGBYTES" and served.headers["content-type"] == "image/jpeg"
    assert (await client.get(f"/api/projects/{pid}/media/{row['id']}")).status_code == 401
    assert (await client.get("/api/projects", headers=ADMIN)).json()[0]["media_count"] == 1

    assert (await client.post(f"/api/projects/{pid}/media", json={**body, "mime": "application/pdf"}, headers=ADMIN)).status_code == 422
    assert (await client.post(f"/api/projects/{pid}/media", json={**body, "data_base64": "not base64!!"}, headers=ADMIN)).status_code == 422
    assert (await client.put(f"/api/projects/{pid}/image", json={"url": "https://dev.example/new.jpg"}, headers=ADMIN)).status_code == 200
    assert (await client.put(f"/api/projects/{pid}/image", json={"url": "javascript:alert(1)"}, headers=ADMIN)).status_code == 422
    assert (await client.delete(f"/api/projects/{pid}/media/{row['id']}", headers=ADMIN)).status_code == 200
    assert (await client.delete(f"/api/projects/{pid}/media/{row['id']}", headers=ADMIN)).status_code == 404

    status = (await client.get("/api/market-sweep", headers=ADMIN)).json()
    assert status["sweep"]["running"] is False and {"developer": "noah", "url": "https://noahrealestateplc.com/project-updates/list/6"} in status["sources"]
