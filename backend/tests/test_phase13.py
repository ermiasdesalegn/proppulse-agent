"""Browser rendering, politeness, scrape health, selector suggestions and failure alerts."""
import sys

import httpx
import pytest

from app.ingest.browser import BrowserFetcher, Politeness
from app.ingest.fetch import FetchError, HttpFetcher
from app.ingest.health import judge
from app.ingest.onboard import ProfileSuggestion, skeleton
from app.ingest.sample import sample_listing
from app.main import create_app
from tests.agent_helpers import FakeGateway, make_chat
from tests.test_ingest_service import FakeFetcher, make

ADMIN = {"X-Admin-Key": "test-admin-key"}
OVERRIDES = dict(admin_api_key="test-admin-key", cron_secret="c", broker_telegram_chat_id="555", scrape_failures_before_alert=3)
EMPTY_PAGE = "<html><body><div id='list'>Loading...</div></body></html>"


# ---------------- politeness ----------------

async def robots(text):
    async def fetch(url):
        if text is None:
            raise FetchError("404")
        return text
    return fetch


async def test_robots_txt_is_obeyed_and_a_missing_one_forbids_nothing():
    polite = Politeness(await robots("User-agent: *\nDisallow: /private/"), min_interval=0)
    await polite.wait_turn("https://dev.example/units")
    with pytest.raises(FetchError, match="robots.txt"):
        await polite.wait_turn("https://dev.example/private/list")
    await Politeness(await robots(None), min_interval=0).wait_turn("https://dev.example/private/list")


async def test_one_site_is_not_hit_faster_than_the_interval_but_other_sites_are_not_delayed():
    now, slept = [100.0], []

    async def sleep(seconds):
        slept.append(round(seconds, 1))
        now[0] += seconds

    polite = Politeness(await robots(None), min_interval=2.0, clock=lambda: now[0], sleep=sleep)
    await polite.wait_turn("https://a.example/1")
    now[0] += 0.5
    await polite.wait_turn("https://a.example/2")
    await polite.wait_turn("https://b.example/1")
    assert slept == [1.5]


# ---------------- browser fetcher (a fake browser stands in for Chromium) ----------------

class Request:
    def __init__(self, url, kind):
        self.url, self.resource_type = url, kind


class Route:
    def __init__(self, url, kind, log):
        self.request, self._log = Request(url, kind), log

    async def abort(self):
        self._log.append(("abort", self.request.url))

    async def continue_(self):
        self._log.append(("continue", self.request.url))


class FakePage:
    def __init__(self, context):
        self.context = context

    async def goto(self, url, **kw):
        for url_, kind in self.context.requests:
            await self.context.handler(Route(url_, kind, self.context.log))

    async def content(self):
        return "<html><body><article class='unit'>rendered</article></body></html>"


class FakeContext:
    def __init__(self, requests):
        self.requests, self.log, self.handler, self.closed = requests, [], None, False

    async def route(self, pattern, handler):
        self.handler = handler

    async def new_page(self):
        return FakePage(self)

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, requests):
        self.context, self.requests = None, requests

    async def new_context(self, **kw):
        self.context = FakeContext(self.requests)
        return self.context

    async def close(self):
        pass


def browser_fetcher(requests, **kw):
    browser = FakeBrowser(requests)

    async def launch():
        return browser

    async def resolver(host, port):
        return ["10.0.0.5"] if host == "intranet.local" else ["93.184.216.34"]

    return BrowserFetcher(http=HttpFetcher(), launcher=launch, resolver=resolver, **kw), browser


async def test_a_rendered_page_is_returned_and_the_page_cannot_reach_internal_addresses_or_load_images():
    fetcher, browser = browser_fetcher([("https://dev.example/app.js", "script"), ("https://dev.example/photo.png", "image"), ("http://intranet.local/secret", "xhr")])
    assert "rendered" in await fetcher.get_text("https://dev.example/units")
    assert browser.context.log == [("continue", "https://dev.example/app.js"), ("abort", "https://dev.example/photo.png"), ("abort", "http://intranet.local/secret")]
    assert browser.context.closed, "each page gets a fresh context that is always closed"


async def test_the_browser_refuses_a_starting_address_that_is_internal():
    fetcher, _ = browser_fetcher([])
    with pytest.raises(FetchError, match="private"):
        await fetcher.get_text("http://intranet.local/units")


async def test_the_browser_obeys_robots_txt():
    fetcher, _ = browser_fetcher([], politeness=Politeness(await robots("User-agent: *\nDisallow: /"), min_interval=0))
    with pytest.raises(FetchError, match="robots.txt"):
        await fetcher.get_text("https://dev.example/units")


async def test_without_playwright_the_error_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(FetchError, match="playwright install chromium"):
        await BrowserFetcher._playwright_launcher()


# ---------------- reading JavaScript pages through the ingestion service ----------------

PAGE_URL = "https://ayat.example/units"


async def configure(repo, mode):
    html, profile = sample_listing(1)
    await repo.set_developer_scrape("ayat", PAGE_URL, profile, mode)
    return html


async def test_auto_mode_tries_the_browser_only_when_the_plain_page_has_no_unit_cards(repo):
    html = await configure(repo, "auto")
    service, *_ = make(repo, fetcher=FakeFetcher(pages={PAGE_URL: EMPTY_PAGE}))
    service.browser = FakeFetcher(pages={PAGE_URL: html})
    report = await service.run(developer_slug="ayat", source="url", dry_run=True)
    assert report["found"] == 4 and any("JavaScript" in n for n in report["notes"])
    plain = FakeFetcher(pages={PAGE_URL: html})
    service2, *_ = make(repo, fetcher=plain)
    service2.browser = FakeFetcher(pages={})  # would fail if used
    assert (await service2.run(developer_slug="ayat", source="url", dry_run=True))["found"] == 4


async def test_static_mode_never_uses_the_browser_and_browser_mode_always_does(repo):
    html = await configure(repo, "static")
    service, *_ = make(repo, fetcher=FakeFetcher(pages={PAGE_URL: EMPTY_PAGE}))
    service.browser = FakeFetcher(pages={PAGE_URL: html})
    assert (await service.run(developer_slug="ayat", source="url", dry_run=True))["found"] == 0
    await configure(repo, "browser")
    assert (await service.run(developer_slug="ayat", source="url", dry_run=True))["found"] == 4
    service.browser = None
    with pytest.raises(FetchError, match="not set up"):
        await service.run(developer_slug="ayat", source="url", dry_run=True)


async def test_the_number_of_unit_cards_found_is_recorded_with_each_real_run(repo):
    html = await configure(repo, "static")
    service, *_ = make(repo, fetcher=FakeFetcher(pages={PAGE_URL: html}))
    await service.run(developer_slug="ayat", source="url")
    dev = await repo.get_developer_by_slug("ayat")
    assert (await repo.recent_page_runs(dev["id"]))[0]["items_found"] == 4


# ---------------- health ----------------

def run_row(found=4, error=None):
    return {"items_found": found, "error": error, "created_at": None}


def test_health_judges_recent_runs():
    assert judge([])["status"] == "never_run"
    assert judge([run_row(4)])["status"] == "ok"
    assert judge([run_row(0), run_row(4)])["status"] == "layout_changed"
    assert judge([run_row(1), run_row(4)])["status"] == "layout_changed", "fewer than half of what it used to find"
    assert judge([run_row(3), run_row(4)])["status"] == "ok"
    assert judge([run_row(None, "boom")])["status"] == "warning"
    three = judge([run_row(None, "boom")] * 3 + [run_row(4)])
    assert three["status"] == "failing" and three["consecutive_failures"] == 3 and "boom" in three["message"]
    assert judge([run_row(4), run_row(None, "old failure")])["status"] == "ok", "a success ends the streak"


# ---------------- selector suggestions ----------------

def test_the_page_is_reduced_to_structure_before_it_goes_to_the_model():
    html, _ = sample_listing(1)
    small = skeleton("<html><head><title>x</title><script>secret()</script></head><body style='a' onclick='b()'>" + html + "</body></html>")
    assert "secret" not in small and "onclick" not in small and "article" in small and 'class="project"' in small


async def client_for(repo, gateway=None, fetcher=None):
    chat, services, _ = make_chat(repo, FakeGateway(), **OVERRIDES)
    service, gw, _ = make(repo, gateway=gateway, fetcher=fetcher, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.ingest = chat.settings, repo, chat, service
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), services, gw


async def test_a_suggestion_is_tested_on_the_page_and_nothing_is_saved(repo):
    html, profile = sample_listing(1)
    gw = FakeGateway([ProfileSuggestion(item=profile["item"], fields=profile["fields"], reasoning="cards are article.unit")])
    client, *_ = await client_for(repo, gateway=gw)
    async with client:
        res = await client.post("/api/developers/ayat/suggest-profile", json={"html": html}, headers=ADMIN)
    body = res.json()
    assert res.status_code == 200 and body["usable"] and body["found"] == 4 and body["preview"][0]["title"].startswith("Ayat")
    assert (await repo.get_developer_by_slug("ayat"))["scrape_profile"] is None


async def test_a_bad_suggestion_is_reported_as_unusable_not_as_a_crash(repo):
    gw = FakeGateway([ProfileSuggestion(item="div.nothing-like-this", fields={"price": ".p"})])
    client, *_ = await client_for(repo, gateway=gw)
    async with client:
        body = (await client.post("/api/developers/ayat/suggest-profile", json={"html": sample_listing(1)[0]}, headers=ADMIN)).json()
        assert body["usable"] is False and body["found"] == 0
        assert (await client.post("/api/developers/ayat/suggest-profile", json={}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/developers/nobody/suggest-profile", json={"html": "x"}, headers=ADMIN)).status_code == 404
        assert (await client.post("/api/developers/ayat/suggest-profile", json={"html": "x"})).status_code == 401


# ---------------- failures: history and a single alert ----------------

async def test_failed_scheduled_reads_are_recorded_and_the_broker_is_told_exactly_once(repo):
    _, profile = sample_listing(1)
    await repo.set_developer_scrape("ayat", PAGE_URL, profile)
    client, services, _ = await client_for(repo, fetcher=FakeFetcher(pages={}))  # every fetch fails
    async with client:
        for _ in range(4):
            await client.post("/api/cron/scrape-market", headers=ADMIN)
        health = {h["slug"]: h for h in (await client.get("/api/scrape/health", headers=ADMIN)).json()}
        assert (await client.get("/api/scrape/health")).status_code == 401
    alerts = [m for m in services.telegram.messages if "Scraper alert" in m["text"]]
    assert len(alerts) == 1 and alerts[0]["chat_id"] == "555" and "Ayat" in alerts[0]["text"]
    assert health["ayat"]["status"] == "failing" and health["ayat"]["consecutive_failures"] == 4 and health["ayat"]["fetch_mode"] == "auto"


async def test_previews_leave_no_failure_record(repo):
    _, profile = sample_listing(1)
    await repo.set_developer_scrape("ayat", PAGE_URL, profile)
    client, *_ = await client_for(repo, fetcher=FakeFetcher(pages={}))
    async with client:
        await client.post("/api/cron/scrape-market?dry_run=true", headers=ADMIN)
    assert await repo.recent_page_runs((await repo.get_developer_by_slug("ayat"))["id"]) == []


async def test_the_fetch_mode_is_saved_and_validated(repo):
    _, profile = sample_listing(1)
    client, *_ = await client_for(repo)
    async with client:
        ok = await client.put("/api/developers/ayat/scrape", json={"url": PAGE_URL, "profile": profile, "fetch_mode": "browser"}, headers=ADMIN)
        assert ok.status_code == 200 and ok.json()["fetch_mode"] == "browser"
        assert (await client.put("/api/developers/ayat/scrape", json={"url": PAGE_URL, "profile": profile, "fetch_mode": "magic"}, headers=ADMIN)).status_code == 422
        kept = await client.put("/api/developers/ayat/scrape", json={"url": PAGE_URL, "profile": profile}, headers=ADMIN)
        assert kept.json()["fetch_mode"] == "browser", "leaving it out keeps the current mode"


async def test_the_demo_page_builds_its_cards_with_a_script(repo):
    client, *_ = await client_for(repo)
    async with client:
        html = (await client.get("/demo/js-listing")).text
    assert "<script>" in html and '<article class="unit">' not in html.split("<script>")[0]
