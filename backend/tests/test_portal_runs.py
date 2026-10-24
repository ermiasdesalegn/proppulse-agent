"""Portal crawl runs: starting one from the dashboard, the runner's reports, the alerts, and the booking path for units read from a portal."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import get_settings
from app.portal_runs import PORTALS, GitHubDispatcher, PortalRunError, PortalRunService
from app.services.email import MockEmailService
from tests.agent_helpers import FakeGateway, ext, make_agent, make_services, new_buyer, say_to
from tests.test_agent_graph import BOLE_3BR, BOOK
from tests.test_api_ingest import ADMIN, build


class FakeProactive:
    def __init__(self):
        self.sent = []

    async def notify_broker(self, kind, key, text):
        self.sent.append((kind, key, text))
        return True


class FakeDispatcher:
    enabled = True

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def dispatch(self, inputs):
        if self.fail:
            raise PortalRunError(self.fail)
        self.calls.append(inputs)


def make(repo, dispatcher=None, **settings):
    proactive = FakeProactive()
    service = PortalRunService(repo, get_settings().model_copy(update=settings), proactive=proactive, dispatcher=dispatcher or FakeDispatcher())
    return service, proactive


# ---------------- GitHub ----------------

async def test_the_dispatch_call_carries_the_token_and_the_inputs():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], request.read().decode()
        return httpx.Response(204)

    settings = get_settings().model_copy(update={"github_token": "t0k", "github_repo": "me/repo", "github_workflow": "portal-crawl.yml", "github_ref": "main"})
    await GitHubDispatcher(settings, transport=httpx.MockTransport(handler)).dispatch({"site": "all", "limit": "50", "dry_run": "true", "request_id": "abc"})
    assert seen["url"] == "https://api.github.com/repos/me/repo/actions/workflows/portal-crawl.yml/dispatches"
    assert seen["auth"] == "Bearer t0k" and '"ref":"main"' in seen["body"].replace(" ", "") and '"request_id":"abc"' in seen["body"].replace(" ", "")


@pytest.mark.parametrize("status,words", [(401, "wrong or expired"), (403, "Actions"), (404, "not found"), (422, "inputs"), (500, "500")])
async def test_github_refusals_become_plain_messages(status, words):
    settings = get_settings().model_copy(update={"github_token": "t", "github_repo": "me/repo"})
    dispatcher = GitHubDispatcher(settings, transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(PortalRunError, match=words):
        await dispatcher.dispatch({})


async def test_without_a_token_the_dashboard_says_what_is_missing():
    with pytest.raises(PortalRunError, match="GITHUB_TOKEN"):
        await GitHubDispatcher(get_settings().model_copy(update={"github_token": "", "github_repo": ""})).dispatch({})


# ---------------- starting a crawl ----------------

async def test_starting_a_crawl_queues_a_row_per_portal_and_asks_github(repo):
    dispatcher = FakeDispatcher()
    service, _ = make(repo, dispatcher)
    out = await service.start(site="all", limit=50, dry_run=True)
    assert out["portals"] == list(PORTALS) and dispatcher.calls == [{"site": "all", "limit": "50", "dry_run": "true", "request_id": out["request_id"]}]
    rows = [r for r in await service.runs() if r["request_id"] == out["request_id"]]
    assert len(rows) == len(PORTALS) and {r["status"] for r in rows} == {"QUEUED"} and {r["trigger"] for r in rows} == {"dashboard"}
    assert rows[0]["mode"] == "limited" and rows[0]["page_limit"] == 50


async def test_a_refused_dispatch_marks_the_queued_rows_failed_with_the_reason(repo):
    service, _ = make(repo, FakeDispatcher(fail="GitHub refused to start the crawl: the GitHub token is wrong or expired."))
    with pytest.raises(PortalRunError, match="wrong or expired"):
        await service.start(site="guzohomes", limit=0, dry_run=False)
    row = (await service.runs())[0]
    assert row["status"] == "FAILED" and "wrong or expired" in row["error"] and row["mode"] == "full"


async def test_an_unknown_portal_is_refused(repo):
    service, _ = make(repo)
    with pytest.raises(PortalRunError, match="Unknown portal"):
        await service.start(site="nope", limit=10, dry_run=True)


async def test_a_queued_run_nobody_picked_up_is_flagged_after_twenty_minutes(repo):
    service, _ = make(repo)
    await service.start(site="addislist", limit=10, dry_run=True)
    assert (await service.runs())[0]["waiting_too_long"] is False
    service.now = lambda: datetime.now(UTC) + timedelta(minutes=25)
    assert (await service.runs())[0]["waiting_too_long"] is True


# ---------------- the runner reports ----------------

GOOD = {"pages": 40, "local_price": 38, "no_local_price": 2, "created": 5, "price_changed": 2, "retired": 1, "skipped": 3, "model_reads": 2}


async def test_the_runner_claims_the_dashboards_queued_row_and_a_scheduled_run_opens_its_own(repo):
    service, _ = make(repo)
    out = await service.start(site="guzohomes", limit=20, dry_run=True)
    started = await service.report_start(portal="guzohomes", request_id=out["request_id"], trigger="dashboard", run_url="https://gh/run/1", dry_run=True, mode="limited", page_limit=20)
    rows = await service.runs()
    assert len([r for r in rows if r["portal"] == "guzohomes"]) == 1 and rows[0]["id"] == started["run_id"]
    assert rows[0]["status"] == "RUNNING" and rows[0]["run_url"] == "https://gh/run/1"
    scheduled = await service.report_start(portal="livingethio", request_id=None, trigger="schedule", run_url=None, dry_run=False, mode="incremental", page_limit=300)
    assert (await service.runs())[0]["id"] == scheduled["run_id"] and (await service.runs())[0]["trigger"] == "schedule"
    with pytest.raises(PortalRunError):
        await service.report_start(portal="nope", request_id=None, trigger="manual", run_url=None, dry_run=True, mode="full", page_limit=None)


async def start_run(service, portal="guzohomes", trigger="dashboard", mode="limited", dry_run=False):
    return (await service.report_start(portal=portal, request_id=None, trigger=trigger, run_url=None, dry_run=dry_run, mode=mode, page_limit=None))["run_id"]


async def test_a_clean_run_is_finished_and_a_dashboard_run_gets_a_summary(repo):
    service, proactive = make(repo)
    run = await start_run(service)
    out = await service.report_finish(run_id=run, stats=GOOD, blocked_reason=None, error=None)
    assert out["status"] == "FINISHED" and out["success_rate"] == pytest.approx(0.95)
    assert [k for k, _, _ in proactive.sent] == ["PORTAL_ALERT"] and "5 new" in proactive.sent[0][2] and "2 Gemini calls" in proactive.sent[0][2]


async def test_a_quiet_scheduled_run_sends_nothing_but_one_that_changed_something_does(repo):
    service, proactive = make(repo)
    quiet = await start_run(service, trigger="schedule", mode="incremental")
    await service.report_finish(run_id=quiet, stats={**GOOD, "created": 0, "price_changed": 0, "retired": 0}, blocked_reason=None, error=None)
    assert proactive.sent == []
    busy = await start_run(service, trigger="schedule", mode="incremental")
    await service.report_finish(run_id=busy, stats=GOOD, blocked_reason=None, error=None)
    assert len(proactive.sent) == 1


async def test_a_block_is_reported_as_blocked_and_the_broker_is_told(repo):
    service, proactive = make(repo)
    run = await start_run(service, portal="addislist")
    out = await service.report_finish(run_id=run, stats={"pages": 0}, blocked_reason="the site answered with a bot challenge", error=None)
    assert out["status"] == "BLOCKED" and "Addis List" in proactive.sent[0][2] and "not worked around" in proactive.sent[0][2]


async def test_a_failed_run_and_a_full_crawl_that_read_nothing_are_failures_but_an_empty_incremental_run_is_not(repo):
    service, proactive = make(repo)
    r1 = await start_run(service)
    assert (await service.report_finish(run_id=r1, stats={"pages": 3, "batches_failed": 1}, blocked_reason=None, error="The backend rejected 1 batch(es)."))["status"] == "FAILED"
    r2 = await start_run(service, mode="full")
    assert (await service.report_finish(run_id=r2, stats={"pages": 0}, blocked_reason=None, error=None))["status"] == "FAILED"
    r3 = await start_run(service, trigger="schedule", mode="incremental")
    assert (await service.report_finish(run_id=r3, stats={"pages": 0}, blocked_reason=None, error=None))["status"] == "FINISHED"
    assert len(proactive.sent) == 2 and all("crawl failed" in t for _, _, t in proactive.sent)


async def test_a_collapse_in_locally_priced_pages_tells_the_broker_the_layout_changed(repo):
    service, proactive = make(repo)
    first = await start_run(service)
    await service.report_finish(run_id=first, stats=GOOD, blocked_reason=None, error=None)
    proactive.sent.clear()
    second = await start_run(service)
    out = await service.report_finish(run_id=second, stats={**GOOD, "local_price": 8, "no_local_price": 32}, blocked_reason=None, error=None)
    assert out["status"] == "FINISHED"
    rate_notice = next(t for _, _, t in proactive.sent if "layout" in t)
    assert "20%" in rate_notice and "95% last time" in rate_notice
    small = await start_run(service)
    proactive.sent.clear()
    await service.report_finish(run_id=small, stats={"pages": 4, "local_price": 0, "no_local_price": 4}, blocked_reason=None, error=None)
    assert not any("layout" in t for _, _, t in proactive.sent), "four pages are too few to judge a site"


async def test_the_portal_list_matches_what_the_crawler_reads():
    from scraper.sites import SITES

    assert {slug: site.name for slug, site in SITES.items()} == PORTALS


# ---------------- the HTTP endpoints ----------------

async def test_the_portal_endpoints_need_the_admin_key_and_run_the_whole_flow(spy_repo):
    client, _ = await build(spy_repo)
    dispatcher = FakeDispatcher()
    client._transport.app.state.github = dispatcher
    async with client:
        for method, path in (("GET", "/api/portal/runs"), ("POST", "/api/portal/runs"), ("POST", "/api/portal/runs/report")):
            assert (await client.request(method, path, json={})).status_code == 401
        started = await client.post("/api/portal/runs", json={"site": "guzohomes", "limit": 10, "dry_run": True}, headers=ADMIN)
        assert started.status_code == 200 and dispatcher.calls[0]["site"] == "guzohomes"
        listing = (await client.get("/api/portal/runs", headers=ADMIN)).json()
        assert listing["config"]["can_start"] and listing["runs"][0]["status"] == "QUEUED" and listing["runs"][0]["portal_name"] == "GuzoHomes"
        begin = await client.post("/api/portal/runs/report", json={"phase": "start", "portal": "guzohomes", "request_id": started.json()["request_id"], "trigger": "dashboard",
                                                                  "dry_run": True, "mode": "limited", "page_limit": 10}, headers=ADMIN)
        end = await client.post("/api/portal/runs/report", json={"phase": "finish", "portal": "guzohomes", "run_id": begin.json()["run_id"], "stats": GOOD}, headers=ADMIN)
        assert end.json()["status"] == "FINISHED"
        assert (await client.post("/api/portal/runs/report", json={"phase": "finish", "portal": "guzohomes"}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/portal/runs", json={"site": "Bad Slug"}, headers=ADMIN)).status_code == 422
        dispatcher.fail = "GitHub refused to start the crawl: the GitHub token is wrong or expired."
        assert (await client.post("/api/portal/runs", json={"site": "all"}, headers=ADMIN)).status_code == 502


# ---------------- booking a unit read from a portal ----------------

async def test_a_portal_unit_is_never_registered_by_email_the_broker_is_told_and_the_buyer_is_not_sent_away(spy_repo, db_conn, vector_of):
    await db_conn.execute("update developers set sales_email = 'listings@gift.invalid' where slug = 'gift'")
    await db_conn.execute("update properties set source_url = 'https://portal.test/for-sale/777-flat' "
                          "where developer_id = (select id from developers where slug = 'gift')")
    email = MockEmailService()
    services = make_services(email=email)
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services, broker_telegram_chat_id="555"), await new_buyer(spy_repo, "portalbook")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book 1 and 2 tomorrow at 10")

    assert res.state["booked"] and res.state["lead_reg_failed"] == []
    assert [m["to"] for m in email.sent if m["idempotency_key"] and m["idempotency_key"].startswith("lead-")] == ["sales@noah.example"]
    assert "arranging the viewing" in res.reply and "portal.test" not in res.reply, "the buyer is told the broker will arrange it, and never sent to the portal"
    ledger = [r for r in await spy_repo.list_ledger() if r["buyer_id"] == buyer["id"]]
    assert len(ledger) == 1, "no commission row for a portal unit"
    notice = [m for m in services.telegram.messages if m["chat_id"] == "555" and "Portal viewing to arrange" in m["text"]]
    assert len(notice) == 1 and "https://portal.test/for-sale/777-flat" in notice[0]["text"] and "+251911000000" in notice[0]["text"]
    event = next(iter(services.calendar.events.values()))
    assert "Listed at: https://portal.test/for-sale/777-flat" in str(event)
