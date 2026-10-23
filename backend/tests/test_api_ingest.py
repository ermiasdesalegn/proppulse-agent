import httpx
import pytest
import pytest_asyncio

from app.ingest.fetch import FetchError
from app.ingest.sample import sample_listing
from app.main import create_app
from tests.agent_helpers import FakeGateway, make_chat
from tests.test_ingest_service import FakeFetcher, make

ADMIN = {"X-Admin-Key": "test-admin-key"}
CRON = {"X-Cron-Secret": "test-cron-secret"}
OVERRIDES = dict(admin_api_key="test-admin-key", cron_secret="test-cron-secret")


async def build(repo, gateway=None, fetcher=None):
    chat, _, _ = make_chat(repo, FakeGateway(), **OVERRIDES)
    service, gw, _ = make(repo, gateway=gateway, fetcher=fetcher, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.ingest = chat.settings, repo, chat, service
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), gw


@pytest_asyncio.fixture
async def api(spy_repo):
    client, gw = await build(spy_repo)
    async with client:
        yield client, gw


def body(variant=2, **over):
    html, profile = sample_listing(variant)
    return {"developer_slug": "ayat", "source": "html", "html": html, "profile": profile, **over}


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/ingest"), ("GET", "/api/ingest/runs"), ("GET", "/api/samples/listing"), ("GET", "/api/developers"),
    ("PUT", "/api/developers/ayat/scrape"),
])
async def test_ingestion_endpoints_need_the_admin_key(api, method, path):
    client, _ = api
    assert (await client.request(method, path, json={})).status_code == 401
    assert (await client.request(method, path, json={}, headers=CRON)).status_code == 401


async def test_the_scheduled_scrape_accepts_the_cron_secret_or_admin_key_only(api):
    client, _ = api
    assert (await client.post("/api/cron/scrape-market")).status_code == 401
    assert (await client.post("/api/cron/scrape-market", headers={"X-Cron-Secret": "wrong"})).status_code == 401
    assert (await client.post("/api/cron/scrape-market", headers=CRON)).status_code == 200
    assert (await client.post("/api/cron/scrape-market", headers=ADMIN)).status_code == 200


async def test_a_preview_over_http_returns_the_full_report_and_writes_nothing(api):
    client, gw = api
    res = await client.post("/api/ingest", json=body(2, dry_run=True, full_sync=True), headers=ADMIN)
    assert res.status_code == 200
    report = res.json()
    assert report["dry_run"] is True and report["counts"]["created"] == 1 and report["counts"]["price_changed"] == 1 and report["counts"]["removed"] == 1
    assert report["ads"] == [] and gw.calls == []
    runs = (await client.get("/api/ingest/runs", headers=ADMIN)).json()
    assert runs == [] or all(r["dry_run"] is False for r in runs), "previews are not recorded as runs"


async def test_applying_over_http_is_recorded_in_the_run_history(api):
    client, _ = api
    report = (await client.post("/api/ingest", json=body(2, full_sync=True), headers=ADMIN)).json()
    assert report["run_id"] and report["counts"]["created"] == 1
    runs = (await client.get("/api/ingest/runs", headers=ADMIN)).json()
    assert runs[0]["id"] == report["run_id"] and runs[0]["developer_name"] == "Ayat Real Estate" and runs[0]["created"] == 1


async def test_the_sample_page_endpoint_serves_both_variants(api):
    client, _ = api
    one = (await client.get("/api/samples/listing", headers=ADMIN)).json()
    two = (await client.get("/api/samples/listing", params={"variant": 2}, headers=ADMIN)).json()
    assert one["developer_slug"] == "ayat" and "Ayat Square Studios" in one["html"] and "Ayat Square Studios" not in two["html"]
    assert one["profile"]["item"] == "article.unit"
    assert (await client.get("/api/samples/listing", params={"variant": 9}, headers=ADMIN)).status_code == 422


@pytest.mark.parametrize("override", [
    {"developer_slug": "Bad Slug!"}, {"developer_slug": "a"}, {"source": "carrier-pigeon"}, {"html": "x" * 500_001},
    {"text": "x" * 20_001}, {"url": "h" * 2049},
])
async def test_ingest_rejects_malformed_requests(api, override):
    client, _ = api
    assert (await client.post("/api/ingest", json={**body(), **override}, headers=ADMIN)).status_code == 422


async def test_ingest_maps_domain_errors_to_sensible_statuses(spy_repo):
    gw = FakeGateway()
    gw.fail_listing_extraction = True
    client, _ = await build(spy_repo, gateway=gw)
    async with client:
        assert (await client.post("/api/ingest", json={**body(), "developer_slug": "nobody"}, headers=ADMIN)).status_code == 404
        assert (await client.post("/api/ingest", json={"developer_slug": "ayat", "source": "html"}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/ingest", json={"developer_slug": "ayat", "source": "url", "url": "https://nowhere.example/"}, headers=ADMIN)).status_code == 422
        down = await client.post("/api/ingest", json={"developer_slug": "ayat", "source": "text", "text": "new units"}, headers=ADMIN)
        assert down.status_code == 502 and "language model" in down.json()["detail"] and "gemini down" not in down.text


async def test_the_scrape_source_can_be_saved_validated_and_cleared(api):
    client, _ = api
    profile = sample_listing(1)[1]
    saved = await client.put("/api/developers/ayat/scrape", json={"url": "https://ayat.example/units", "profile": profile}, headers=ADMIN)
    assert saved.status_code == 200 and saved.json()["scrape_url"] == "https://ayat.example/units"
    listing = {d["slug"]: d for d in (await client.get("/api/developers", headers=ADMIN)).json()}
    assert listing["ayat"]["scrape_profile"]["item"] == "article.unit" and listing["noah"]["scrape_url"] is None
    cleared = await client.put("/api/developers/ayat/scrape", json={}, headers=ADMIN)
    assert cleared.status_code == 200 and cleared.json()["scrape_url"] is None

    assert (await client.put("/api/developers/ayat/scrape", json={"url": "file:///etc/passwd", "profile": profile}, headers=ADMIN)).status_code == 422
    assert (await client.put("/api/developers/ayat/scrape", json={"url": "https://x.example"}, headers=ADMIN)).status_code == 422, "an address without selectors is useless"
    assert (await client.put("/api/developers/ayat/scrape", json={"url": "https://x.example", "profile": {"item": ""}}, headers=ADMIN)).status_code == 422
    assert (await client.put("/api/developers/nobody/scrape", json={"url": "https://x.example", "profile": profile}, headers=ADMIN)).status_code == 404


async def test_the_scheduled_scrape_says_so_when_nothing_is_configured(api):
    client, _ = api
    job = (await client.post("/api/cron/scrape-market", headers=CRON)).json()["jobs"][0]
    assert job["job"] == "market_scraper" and "No developer has a scrape address" in job["details"][0]["note"]


async def test_the_scheduled_scrape_reads_every_configured_developer_and_isolates_failures(spy_repo):
    html, profile = sample_listing(2)
    fetcher = FakeFetcher(pages={"https://ayat.example/units": html})  # Noah's address is not served: that fetch fails
    client, _ = await build(spy_repo, fetcher=fetcher)
    async with client:
        await spy_repo.set_developer_scrape("ayat", "https://ayat.example/units", profile)
        await spy_repo.set_developer_scrape("noah", "https://noah.example/units", profile)
        result = (await client.post("/api/cron/scrape-market", params={"dry_run": "true"}, headers=CRON)).json()
    details = {d["developer"]: d for d in result["jobs"][0]["details"]}
    assert details["Ayat Real Estate"] == {"developer": "Ayat Real Estate", "new": 1, "price changes": 1, "removed": 1, "unchanged": 2}
    assert "404" in details["Noah Real Estate"]["error"]
    assert (result["sent"], result["failed"], result["dry_run"]) == (3, 1, True)


# ---------------- floor-plan upload over http ----------------

import base64

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()


def announcement(**over):
    return {"developer_slug": "ayat", "source": "text", "text": "Ayat announces Lake View", "dry_run": True, "floor_plan_base64": PNG, "floor_plan_mime": "image/png", **over}


@pytest.mark.parametrize("override", [
    {"floor_plan_base64": "not base64!!"},
    {"floor_plan_base64": base64.b64encode(b"GIF89a-not-a-png").decode()},
    {"floor_plan_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 5_000_001).decode()},
    {"floor_plan_mime": None},
    {"floor_plan_mime": "image/gif"},
    {"source": "html", "html": "<p>x</p>"},
])
async def test_a_bad_floor_plan_upload_is_rejected(api, override):
    client, _ = api
    assert (await client.post("/api/ingest", json=announcement(**override), headers=ADMIN)).status_code == 422


async def test_a_valid_floor_plan_upload_is_accepted(spy_repo):
    from app.ingest.models import ExtractedUnit, ListingExtraction

    gw = FakeGateway()
    gw.listing_extraction = ListingExtraction(units=[ExtractedUnit(project_name="Ayat Lake View", unit_type="3BR Apartment", sub_city="Ayat", bedrooms=3, sqm=130, price_etb=11_900_000)])
    client, _ = await build(spy_repo, gateway=gw)
    async with client:
        res = await client.post("/api/ingest", json=announcement(), headers=ADMIN)
    assert res.status_code == 200 and res.json()["floor_plan_for"] == "Ayat Lake View 3BR Apartment"
