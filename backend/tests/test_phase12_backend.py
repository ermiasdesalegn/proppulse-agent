"""Explainable lead score, tool tracing, analytics, calendar cancel and reset-demo."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import analytics
from app.agent import scoring
from app.demo import reset_demo
from app.events import EventBus
from app.main import create_app
from app.services.routing import MockRoutingService, Stop
from app.tracing import Traced, plain, summarize_args, summarize_result, trace_repo, trace_services
from tests.agent_helpers import NOW, FakeGateway, ext, make_agent, make_chat, make_services, new_buyer, say_to
from tests.test_agent_graph import BOLE_3BR
from tests.test_phase10_agent import CONTACT

ADMIN = {"X-Admin-Key": "test-admin-key"}
PREFS = {"max_budget_etb": 15_000_000, "min_bedrooms": 2, "preferred_sub_cities": ["Bole"]}
NONE = {"max_budget_etb": None, "min_bedrooms": None, "preferred_sub_cities": []}


# ---------------- the score and its reasons ----------------

def test_the_signals_add_up_to_exactly_one_hundred():
    assert scoring.MAXIMUM == 100
    everything = scoring.signals({"full_name": "A", "phone": "1", "email": "e"}, PREFS, "book_tour", "high")
    assert scoring.total(everything) == 100 and set(everything) == {"name", "phone", "email", "budget", "bedrooms", "areas", "intent", "urgency"}


def test_nothing_said_scores_nothing_and_each_signal_is_named():
    assert scoring.signals({}, NONE, "small_talk", "low") == {} and scoring.total({}) == 0
    got = scoring.signals({"phone": "1"}, NONE, "compare", "medium")
    assert got == {"phone": {"points": 15, "label": "Shared a phone number"}, "intent": {"points": 8, "label": "Compared options"},
                   "urgency": {"points": 5, "label": "Wants to act within weeks"}}


def test_a_consultation_request_is_worth_less_than_asking_to_visit():
    assert scoring.signals({}, NONE, "book_consultation", "low")["intent"]["points"] == 15 < scoring.signals({}, NONE, "book_tour", "low")["intent"]["points"]


def test_earned_signals_are_kept_and_only_the_better_version_replaces_one():
    first = scoring.signals({}, NONE, "compare", "high")
    later = scoring.merge(first, scoring.signals({"phone": "1"}, NONE, "small_talk", "low"))
    assert set(later) == {"intent", "urgency", "phone"}, "saying thank you does not undo interest"
    upgraded = scoring.merge(later, scoring.signals({}, NONE, "book_tour", "low"))
    assert upgraded["intent"] == {"points": 20, "label": "Asked to visit a property"} and upgraded["urgency"]["points"] == 10
    assert scoring.merge(upgraded, scoring.signals({}, NONE, "compare", "low"))["intent"]["points"] == 20, "a weaker signal never replaces a stronger one"


def test_components_list_every_signal_earned_or_not():
    parts = scoring.components(scoring.signals({"full_name": "A"}, PREFS, "small_talk", "low"))
    assert [p["key"] for p in parts] == ["name", "phone", "email", "budget", "bedrooms", "areas", "intent", "urgency"]
    assert {p["key"] for p in parts if p["earned"]} == {"name", "budget", "bedrooms", "areas"}
    assert sum(p["points"] for p in parts) == 10 + 20 + 10 + 10 and sum(p["max"] for p in parts) == 100


def test_what_a_message_added_is_reported_for_the_history():
    before = scoring.signals({}, PREFS, "compare", "low")
    after = scoring.merge(before, scoring.signals({"phone": "1"}, PREFS, "book_tour", "low"))
    assert sorted(scoring.gained(before, after), key=lambda g: g["label"]) == [{"label": "Asked to visit a property", "points": 12}, {"label": "Shared a phone number", "points": 15}]
    assert scoring.gained(after, after) == []


async def test_the_score_is_stored_with_its_reasons_and_its_history(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("small_talk"), ext("book_tour", referenced_options=[1], **CONTACT)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "p12-score")
    await say_to(agent, buyer, "3BR in Bole, 25M")
    row = await spy_repo.get_buyer(buyer["id"])
    assert set(row["score_breakdown"]) == {"budget", "bedrooms", "areas"} and row["lead_score"] == 40
    await say_to(agent, buyer, "thanks")
    assert [h["score"] for h in await spy_repo.score_history(buyer["id"])] == [40], "a message that earns nothing adds no history"
    await say_to(agent, buyer, "book the first one")
    history = await spy_repo.score_history(buyer["id"])
    assert [h["score"] for h in history] == [40, 90] and {g["label"] for g in history[1]["gained"]} >= {"Gave their name", "Asked to visit a property"}
    assert history[0]["gained"][0]["points"] > 0 and (await spy_repo.get_buyer(buyer["id"]))["lead_score"] == 90


async def test_a_score_from_before_breakdowns_existed_is_never_lowered(spy_repo, vector_of):
    buyer = await new_buyer(spy_repo, "p12-legacy")
    await spy_repo.update_buyer(buyer["id"], lead_score=55)
    gw = FakeGateway([ext("small_talk")])
    agent = make_agent(spy_repo, gw)
    await say_to(agent, buyer, "hello")
    assert (await spy_repo.get_buyer(buyer["id"]))["lead_score"] == 55 and await spy_repo.score_history(buyer["id"]) == []


# ---------------- the profile endpoint ----------------

async def client_for(repo, **settings):
    chat, services, _ = make_chat(repo, FakeGateway(), admin_api_key="test-admin-key", cron_secret="c", **settings)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.events = chat.settings, repo, chat, EventBus()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), services, app


async def test_the_profile_explains_the_score_and_lists_objections_and_tours(spy_repo):
    buyer = await new_buyer(spy_repo, "p12-profile")
    earned = scoring.merge({}, scoring.signals({"full_name": "A"}, PREFS, "compare", "low"))
    await spy_repo.update_buyer(buyer["id"], lead_score=scoring.total(earned), score_breakdown=earned)
    await spy_repo.record_score(buyer["id"], 58, scoring.gained({}, earned))
    await spy_repo.add_objections(buyer["id"], ["price feels high"])
    prop = (await spy_repo._fetch("select id from properties limit 1"))[0]["id"]
    await spy_repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW, stops=[prop], route_json=None, calendar_event_id="e")
    client, *_ = await client_for(spy_repo)
    async with client:
        assert (await client.get(f"/api/buyers/{buyer['id']}/profile")).status_code == 401
        body = (await client.get(f"/api/buyers/{buyer['id']}/profile", headers=ADMIN)).json()
        assert (await client.get("/api/buyers/00000000-0000-0000-0000-000000000000/profile", headers=ADMIN)).status_code == 404
    assert body["score"]["total"] == 58 and body["score"]["explained"] == 58 and len(body["score"]["components"]) == 8
    assert body["score"]["history"][0]["score"] == 58 and body["objections"][0]["text"] == "price feels high" and len(body["tours"]) == 1
    assert "preference_embedding" not in body["buyer"]


# ---------------- tool tracing ----------------

def test_summaries_hide_message_bodies_addresses_and_vectors():
    assert plain("abel.tesfaye@example.com") == "a***@example.com"
    assert plain("x" * 200).endswith("(200 chars)") and plain(b"12345") == "5 bytes"
    assert plain([0.1] * 768) == {"vector": 768} and plain(list(range(50))) == {"count": 50, "first": 0}
    assert plain(datetime(2026, 11, 2, 10, tzinfo=UTC)) == "2026-11-02T10:00:00+00:00"
    args = summarize_args((), {"to": "a@b.test", "subject": "Hi", "html": "<p>secret</p>", "text": "hello there"})
    assert args == {"to": "a***@b.test", "subject": "Hi", "html": "13 chars", "text": "11 chars"}


async def test_a_route_plan_and_lists_are_summarised_by_what_matters():
    plan = await MockRoutingService().plan_route([Stop("a", 9.0, 38.7), Stop("b", 9.02, 38.75)])
    assert summarize_result(plan)["stops"] == 2 and summarize_result(plan)["km"] > 0
    assert summarize_result([]) == {"count": 0} and summarize_result([{"x": 1}]) == {"count": 1, "top": {"x": 1}}
    assert summarize_result([{"title": "A"}, {"title": "B"}]) == {"count": 2, "titles": ["A", "B"]}, "search results are reported by what was found"
    from decimal import Decimal
    from uuid import UUID

    assert plain(UUID("c4151ce5-c564-4654-a316-d4b07de6e52a")) == "c4151ce5-c564-4654-a316-d4b07de6e52a" and plain(Decimal("1.5")) == 1.5


class Tool:
    attribute = 7

    async def work(self, x, secret_body=None):
        return [x, x]

    async def fail(self):
        raise RuntimeError("down")

    async def untraced(self):
        return "plain"


async def test_a_traced_call_reports_arguments_result_timing_and_context():
    bus = EventBus()
    tool = Traced(Tool(), "demo", bus, ["work", "fail"])
    from app.gemini import CALL_CONTEXT

    token = CALL_CONTEXT.set({"node": "scheduler", "buyer_id": "b-1"})
    try:
        assert await tool.work(3) == [3, 3]
    finally:
        CALL_CONTEXT.reset(token)
    [event] = bus.subscribe(0).backlog
    assert (event["kind"], event["tool"], event["node"], event["buyer_id"], event["ok"]) == ("tool", "demo.work", "scheduler", "b-1", True)
    assert event["args"] == {"arg1": 3} and event["result"] == {"count": 2, "top": 3} and event["ms"] >= 0


async def test_failures_are_reported_and_still_raised_and_other_attributes_pass_through():
    bus = EventBus()
    tool = Traced(Tool(), "demo", bus, ["fail"])
    with pytest.raises(RuntimeError):
        await tool.fail()
    assert bus.subscribe(0).backlog[0]["ok"] is False and bus.subscribe(0).backlog[0]["error"] == "RuntimeError"
    assert tool.attribute == 7 and await tool.untraced() == "plain" and len(bus.subscribe(0).backlog) == 1, "unlisted calls are not reported"


async def test_a_broken_bus_never_changes_the_outcome_of_a_call():
    class Broken:
        def publish(self, *a, **k):
            raise RuntimeError("bus down")

    assert await Traced(Tool(), "demo", Broken(), ["work"]).work(1) == [1, 1]


def test_without_a_bus_nothing_is_wrapped():
    services = make_services()
    assert trace_services(services, None) is services and trace_repo("repo", None) == "repo"


async def test_a_booking_reports_search_calendar_routing_and_email_calls_with_their_node(spy_repo, vector_of):
    bus = EventBus()
    services = trace_services(make_services(), bus)
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", referenced_options=[1], tour_start_iso="2026-11-03T10:00:00+03:00", **CONTACT)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "p12-trace")
    agent._deps.repo = trace_repo(agent._deps.repo, bus)
    await say_to(agent, buyer, "3BR in Bole")
    await say_to(agent, buyer, "book the first one")
    tools = {(e["tool"], e["node"]) for e in bus.subscribe(0).backlog if e["kind"] == "tool"}
    assert {("calendar.is_available", "scheduler"), ("calendar.book", "scheduler"), ("routing.plan_route", "scheduler"), ("email.send", "scheduler")} <= tools
    send = next(e for e in bus.subscribe(0).backlog if e.get("tool") == "email.send")
    assert "***@" in send["args"]["to"] and send["args"]["html"].endswith("chars"), "addresses are masked and bodies are not copied"


# ---------------- calendar cancel ----------------

async def test_cancelling_removes_the_event_and_a_second_cancel_is_harmless():
    cal = make_services().calendar
    start = NOW.replace(day=3, hour=10)
    event_id = await cal.book(start=start, end=start + timedelta(hours=1), summary="x")
    assert await cal.is_available(start, start + timedelta(hours=1)) is False
    await cal.cancel(event_id)
    await cal.cancel(event_id)
    assert await cal.is_available(start, start + timedelta(hours=1)) is True


# ---------------- analytics ----------------

async def seed_activity(repo):
    a, b = await repo.get_or_create_buyer("test:an-a"), await repo.get_or_create_buyer("test:an-b")
    await repo.update_buyer(a["id"], deal_stage="QUALIFIED", lead_score=45)
    await repo.update_buyer(b["id"], deal_stage="SITE_VISIT_BOOKED", lead_score=92)
    await repo.add_objections(a["id"], ["Price feels high"])
    await repo.add_objections(b["id"], ["price feels high", "delivery risk"])
    await repo.add_message(a["id"], "buyer", "hi")
    await repo.add_message(a["id"], "agent", "hello")
    prop = (await repo._fetch("select id, developer_id from properties limit 1"))[0]
    await repo.create_ledger_rows(buyer_id=b["id"], tour_id=None, entries=[__import__("app.repository", fromlist=["LedgerEntry"]).LedgerEntry(prop["id"], "m1")])
    for model, node, ms, tin, tout, outcome, fallbacks in (("m-a", "router", 1000, 100, 20, "ok", []), ("m-a", "router", 3000, 200, 40, "ok", ["m-big"]), ("m-b", "recommend", 500, 50, 10, "error:X", [])):
        await repo._fetch(
            "insert into model_calls (operation, lane, model, outcome, latency_ms, input_tokens, output_tokens, fallbacks, node, buyer_id) "
            "values ('generate_structured', 'generate', %s, %s, %s, %s, %s, %s, %s, %s) returning id", (model, outcome, ms, tin, tout, fallbacks, node, a["id"]))
    for ms in (1000, 2000, 3000, 4000):
        await repo._fetch("insert into event_log (kind, data) values ('turn_finished', %s) returning id", (json.dumps({"ms": ms}),))
    await repo._fetch("insert into event_log (kind, data) values ('turn_failed', '{}') returning id")
    return a, b


async def test_analytics_summarise_the_funnel_commission_models_latency_and_worries(repo, tmp_path, monkeypatch):
    await seed_activity(repo)
    monkeypatch.setattr(analytics, "EVAL_SUMMARY", tmp_path / "missing.json")
    data = await analytics.build(repo, days=30)
    funnel = {f["stage"]: f["buyers"] for f in data["funnel"]}
    assert funnel["QUALIFIED"] >= 1 and funnel["SITE_VISIT_BOOKED"] >= 1 and [f["stage"] for f in data["funnel"]][0] == "NEW"
    assert data["ledger"][0]["status"] == "LEAD_REGISTERED" and data["ledger"][0]["deals"] >= 1 and data["commission"]["open_etb"] > 0 and data["commission"]["paid_etb"] == 0
    models = {m["model"]: m for m in data["models"]}
    assert models["m-a"]["calls"] == 2 and models["m-a"]["avg_ms"] == 2000 and models["m-a"]["fell_back"] == 1 and models["m-b"]["errors"] == 1
    assert models["m-a"]["input_tokens"] == 300 and models["m-a"]["output_tokens"] == 60
    nodes = {n["node"]: n for n in data["nodes"]}
    assert nodes["router"]["calls"] == 2 and "recommend" not in nodes, "only successful calls are timed per node"
    assert data["turns"]["count"] == 4 and data["turns"]["failed"] == 1 and data["turns"]["p50_ms"] == pytest.approx(2500) and 3000 < data["turns"]["p95_ms"] <= 4000
    assert data["objections"][0] == {"text": "price feels high", "n": 2} and {"text": "delivery risk", "n": 1} in data["objections"]
    assert {(r["role"]) for r in data["messages_per_day"]} >= {"buyer", "agent"} and data["per_conversation"]["buyers"] >= 1
    assert sum(b["buyers"] for b in data["score_distribution"]) >= 2 and any(b["from"] == 80 and b["to"] == 100 for b in data["score_distribution"])
    assert data["evaluation"] is None


async def test_the_evaluation_summary_is_included_when_present(repo, tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"retrieval": {"hit_at_3": 1}}), encoding="utf-8")
    monkeypatch.setattr(analytics, "EVAL_SUMMARY", path)
    assert (await analytics.build(repo, days=7))["evaluation"] == {"retrieval": {"hit_at_3": 1}}


async def test_analytics_endpoint_needs_the_admin_key_and_clamps_the_window(spy_repo):
    client, *_ = await client_for(spy_repo)
    async with client:
        assert (await client.get("/api/analytics")).status_code == 401
        body = (await client.get("/api/analytics?days=100000", headers=ADMIN)).json()
    assert body["days"] == 365 and "funnel" in body


# ---------------- reset demo ----------------

async def dirty_world(repo, services):
    a, _ = await seed_activity(repo)
    start = NOW.replace(day=3, hour=10)
    event_id = await services.calendar.book(start=start, end=start + timedelta(hours=1), summary="tour")
    prop = (await repo._fetch("select id from properties where title = 'Noah Bole Towers 2BR Apartment'"))[0]["id"]
    await repo.create_tour(buyer_id=a["id"], scheduled_start=start, stops=[prop], route_json=None, calendar_event_id=event_id)
    await repo.update_property_price(prop, 9_000_000)
    await repo.as_platform()._fetch(
        "insert into properties (developer_id, title, project_name, unit_type, sub_city, price_etb, bedrooms, sqm, content_hash, embedding, vision_amenities) "
        "select developer_id, 'Scraped Extra', 'Extra', 'Studio', 'Bole', 1, 0, 30, 'extra-hash', embedding, '{\"source\": \"scrape\"}'::jsonb from properties limit 1 returning id")
    await repo.as_platform()._fetch("update developers set payment_plan = null where slug = 'ayat' returning id")
    return event_id, prop


async def test_reset_wipes_activity_restores_the_catalogue_and_cancels_calendar_events(repo):
    services = make_services()
    event_id, prop = await dirty_world(repo, services)
    report = await reset_demo(repo, services.calendar)

    assert report["calendar_events_cancelled"] == 1 and event_id not in services.calendar.events
    for table in ("buyers_crm", "commission_ledger", "tours_and_appointments", "chat_messages", "model_calls", "event_log", "price_history", "lead_score_history"):
        assert (await repo._one(f"select count(*)::int as n from {table}"))["n"] == 0, table
    assert report["deleted"]["added_properties"] == 1 and report["restored_properties"] == 15
    assert float((await repo._one("select price_etb from properties where id = %s", (prop,)))["price_etb"]) == 13_200_000
    assert (await repo._one("select count(*)::int as n from properties where title = 'Scraped Extra'"))["n"] == 0
    assert (await repo._one("select payment_plan from developers where slug = 'ayat'"))["payment_plan"]["installments"] == 8


async def test_reset_keeps_working_when_the_calendar_cannot_cancel(repo):
    class Broken:
        async def cancel(self, event_id):
            raise RuntimeError("calendar down")

    services = make_services()
    await dirty_world(repo, services)
    report = await reset_demo(repo, Broken())
    assert report["calendar_events_cancelled"] == 0 and (await repo._one("select count(*)::int as n from buyers_crm"))["n"] == 0


async def test_the_reset_endpoint_is_off_by_default_and_needs_the_word_reset(spy_repo):
    client, services, app = await client_for(spy_repo)
    async with client:
        assert (await client.post("/api/demo/reset", json={"confirm": "RESET"})).status_code == 401
        off = await client.post("/api/demo/reset", json={"confirm": "RESET"}, headers=ADMIN)
        assert off.status_code == 403 and "switched off" in off.json()["detail"]
        assert (await client.get("/api/stats", headers=ADMIN)).json()["demo_reset_enabled"] is False
        app.state.settings = app.state.settings.model_copy(update={"demo_reset_enabled": True})
        assert (await client.get("/api/stats", headers=ADMIN)).json()["demo_reset_enabled"] is True
        assert (await client.post("/api/demo/reset", json={"confirm": "reset"}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/demo/reset", json={}, headers=ADMIN)).status_code == 422
        await seed_activity(spy_repo)
        done = await client.post("/api/demo/reset", json={"confirm": "RESET"}, headers=ADMIN)
    assert done.status_code == 200 and done.json()["deleted"]["buyers_crm"] >= 2
    assert (await spy_repo._one("select count(*)::int as n from buyers_crm"))["n"] == 0
