"""Full conversations through the real graph, real database (rolled back), scripted LLM, mock services."""
from decimal import Decimal

import pytest

from app.agent.prompts import VOICE_FALLBACK
from app.agent.schemas import Recommendation
from app.services.email import MockEmailService
from app.services.routing import MockRoutingService
from tests.agent_helpers import FakeGateway, ext, make_agent, make_services, new_buyer, say_to

BOLE_3BR = dict(max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3 bedroom apartment in Bole")


async def buyer_row(db_conn, buyer):
    cur = await db_conn.execute("select * from buyers_crm where id = %s", (buyer["id"],))
    return await cur.fetchone()


async def saved_state(agent, buyer):
    return (await agent.graph.aget_state({"configurable": {"thread_id": buyer["thread_id"]}})).values


# ---------------- search, relaxation, waitlist ----------------

async def test_search_returns_matches_and_writes_the_crm(spy_repo, db_conn, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR, full_name="Abel")], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "search")
    res = await say_to(agent, buyer, "I want a 3 bedroom in Bole, 25M")

    titles = [m["title"] for m in res.state["active_matches"]]
    assert titles[0] == "Noah Bole Towers 3BR Apartment" and "Gift Bole Court 3BR Apartment" in titles
    assert res.state["search_scope"]["relaxation_step"] == 0 and len(spy_repo.searches) == 1
    assert res.reply.startswith("INTRO") and res.reply.endswith("CLOSING")
    assert "1. Noah Bole Towers 3BR Apartment - Noah Real Estate, Bole" in res.reply
    assert "19.8M ETB (146,667 ETB/sqm) | 30% down (5.94M ETB) | delivery Mar 2027" in res.reply

    row = await buyer_row(db_conn, buyer)
    assert row["max_budget_etb"] == Decimal("25000000") and row["min_bedrooms"] == 3
    assert row["preferred_sub_cities"] == ["Bole"] and row["full_name"] == "Abel"
    assert row["deal_stage"] == "QUALIFIED" and row["lead_score"] > 0
    cur = await db_conn.execute("select vector_dims(preference_embedding) d, preference_summary s from buyers_crm where id=%s", (buyer["id"],))
    pref = await cur.fetchone()
    assert pref["d"] == 768 and "25M ETB" in pref["s"] and "Bole" in pref["s"]


async def test_step_1_relaxation_finds_a_unit_slightly_over_budget_and_says_so(spy_repo, vector_of):
    gw = FakeGateway([ext(max_budget_etb=16_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole")],
                     vector=await vector_of("Gift Bole Court%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "relax1")
    res = await say_to(agent, buyer, "3 bedroom in Bole under 16M")

    assert [s["max_budget_etb"] for s in spy_repo.searches] == [16_000_000, pytest.approx(17_920_000)]
    assert "Gift Bole Court 3BR Apartment" in [m["title"] for m in res.state["active_matches"]]
    assert res.state["search_scope"]["relaxation_step"] == 1
    assert "IMPORTANT" in gw.prompts("recommendation")[-1] and "widened" in gw.prompts("recommendation")[-1]
    prefs = res.state["buyer_preferences"]
    assert prefs["max_budget_etb"] == 16_000_000 and prefs["preferred_sub_cities"] == ["Bole"], "true preferences must survive"


async def test_step_2_relaxation_drops_the_area_filter(spy_repo, vector_of):
    gw = FakeGateway([ext(max_budget_etb=10_000_000, min_bedrooms=3, preferred_sub_cities=["Ayat"], search_query="3BR family")],
                     vector=await vector_of("Noah Sarbet Gardens%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "relax2")
    res = await say_to(agent, buyer, "3 bedroom in Ayat under 10M")

    assert len(spy_repo.searches) == 3
    assert spy_repo.searches[2]["sub_cities"] == [] and spy_repo.searches[2]["max_budget_etb"] == pytest.approx(12_400_000)
    assert [m["title"] for m in res.state["active_matches"]] == ["Noah Sarbet Gardens 3BR Apartment"]
    assert res.state["search_scope"]["relaxation_step"] == 2


async def test_no_match_after_two_relaxations_goes_to_the_waitlist(spy_repo, db_conn, vector_of):
    opposite = [-x for x in await vector_of("Noah Bole Towers 3BR%")]
    gw = FakeGateway([ext(max_budget_etb=20_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="a farm")], vector=opposite)
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "waitlist")
    res = await say_to(agent, buyer, "a farm in Bole")

    assert len(spy_repo.searches) == 3, "strict + exactly two relaxations, never more"
    assert "Priority Watchlist" in res.reply and "24%" in res.reply
    assert (await buyer_row(db_conn, buyer))["deal_stage"] == "INVENTORY_PENDING"
    assert res.state["active_matches"] == []
    assert gw.count("text") == 0, "the waitlist reply is fixed text, not an LLM call"


async def test_nothing_to_relax_goes_straight_to_the_waitlist(spy_repo, vector_of):
    opposite = [-x for x in await vector_of("Noah Bole Towers 3BR%")]
    gw = FakeGateway([ext(search_query="something")], vector=opposite)
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "norelax")
    res = await say_to(agent, buyer, "show me something")
    assert len(spy_repo.searches) == 1 and "Priority Watchlist" in res.reply and "buffer" not in res.reply


async def test_every_turn_starts_strict_again(spy_repo, vector_of):
    gw = FakeGateway([ext(max_budget_etb=16_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole"), ext()],
                     vector=await vector_of("Gift Bole Court%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "reset")
    await say_to(agent, buyer, "3 bedroom in Bole under 16M")
    first_turn_searches = len(spy_repo.searches)
    res = await say_to(agent, buyer, "anything else?")
    assert spy_repo.searches[first_turn_searches]["max_budget_etb"] == 16_000_000, "turn 2 must not inherit the +12% budget"
    assert spy_repo.searches[first_turn_searches]["sub_cities"] == ["Bole"]
    assert res.state["buyer_preferences"]["max_budget_etb"] == 16_000_000


async def test_worst_case_path_stays_inside_the_recursion_limit(spy_repo, vector_of):
    opposite = [-x for x in await vector_of("Noah Bole Towers 3BR%")]
    gw = FakeGateway([ext(max_budget_etb=1_000_000, preferred_sub_cities=["Bole"], search_query="x")], vector=opposite)
    agent, buyer = make_agent(spy_repo, gw, recursion_limit=10), await new_buyer(spy_repo, "limit")
    assert (await say_to(agent, buyer, "x")).reply is not None  # would raise GraphRecursionError otherwise


# ---------------- memory across turns ----------------

async def test_preferences_and_matches_persist_between_turns(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext(max_budget_etb=30_000_000)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "persist")
    await say_to(agent, buyer, "3BR Bole 25M")
    res = await say_to(agent, buyer, "actually my budget is 30M")
    prefs = res.state["buyer_preferences"]
    assert prefs["max_budget_etb"] == 30_000_000 and prefs["min_bedrooms"] == 3 and prefs["preferred_sub_cities"] == ["Bole"]
    assert len(res.state["messages"]) >= 4


async def test_preference_vector_is_only_re_embedded_when_a_preference_changes(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext(), ext(max_budget_etb=30_000_000)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "diff")
    summaries = lambda: [p for k, p in gw.calls if k == "embed" and "ETB" in p]
    await say_to(agent, buyer, "3BR Bole 25M")
    assert len(summaries()) == 1
    await say_to(agent, buyer, "any other options?")
    assert len(summaries()) == 1, "an unchanged preference must not cost an embedding call"
    await say_to(agent, buyer, "make it 30M")
    assert len(summaries()) == 2 and "30M ETB" in summaries()[1]


async def test_a_restarted_process_recovers_preferences_from_the_crm(spy_repo, vector_of):
    buyer = await new_buyer(spy_repo, "rehydrate")
    await spy_repo.update_buyer(buyer["id"], max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"])
    gw = FakeGateway([ext(search_query="balcony and gym")], vector=await vector_of("Noah Bole Towers 3BR%"))
    res = await say_to(make_agent(spy_repo, gw), buyer, "something with a balcony")  # fresh MemorySaver = lost checkpoint
    assert spy_repo.searches[0]["max_budget_etb"] == 25_000_000 and spy_repo.searches[0]["sub_cities"] == ["Bole"]
    assert res.state["active_matches"]


# ---------------- voice ----------------

async def test_voice_note_is_transcribed_and_handled_like_text(spy_repo, vector_of):
    services = make_services(files={"F1": b"OGG-BYTES"})
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"), transcript="I want a three bedroom in Bole")
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "voice")
    res = await say_to(agent, buyer, voice_file="F1")
    assert gw.audio == [b"OGG-BYTES"] and "three bedroom in Bole" in gw.prompts("structured")[0]
    assert res.state["active_matches"] and res.state["messages"][0].additional_kwargs["source"] == "voice"


@pytest.mark.parametrize("transcript", ["UNINTELLIGIBLE", "", "unintelligible."])
async def test_unusable_transcript_asks_the_buyer_to_type(spy_repo, transcript):
    gw = FakeGateway([ext()], transcript=transcript)
    agent, buyer = make_agent(spy_repo, gw, services=make_services(files={"F1": b"x"})), await new_buyer(spy_repo, "garbled")
    res = await say_to(agent, buyer, voice_file="F1")
    assert res.reply == VOICE_FALLBACK and gw.count("structured") == 0 and not spy_repo.searches


async def test_failed_voice_download_also_falls_back(spy_repo):
    gw = FakeGateway([ext()])
    agent, buyer = make_agent(spy_repo, gw, services=make_services(files={})), await new_buyer(spy_repo, "nofile")
    assert (await say_to(agent, buyer, voice_file="MISSING")).reply == VOICE_FALLBACK


async def test_empty_message_is_not_sent_to_the_model(spy_repo):
    gw = FakeGateway([ext()])
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "empty")
    res = await say_to(agent, buyer, "   ")
    assert "text and voice" in res.reply and gw.count("structured") == 0


# ---------------- other intents and failures ----------------

async def test_small_talk_does_not_search(spy_repo):
    gw = FakeGateway([ext("small_talk")])
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "hi")
    res = await say_to(agent, buyer, "hello")
    assert res.reply == "LLM-REPLY" and not spy_repo.searches and gw.count("embed") == 0


async def test_model_outage_gives_a_polite_reply_instead_of_crashing(spy_repo):
    agent, buyer = make_agent(spy_repo, FakeGateway(fail_extraction=True)), await new_buyer(spy_repo, "outage")
    assert "trouble" in (await say_to(agent, buyer, "hello")).reply


async def test_llm_writing_failure_falls_back_to_a_plain_listing(spy_repo, vector_of):
    class BrokenWriter(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            if schema is Recommendation:
                raise RuntimeError("down")
            return await super().generate_structured(prompt, schema, system=system)

    gw = BrokenWriter([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "fallbacktext")
    res = await say_to(agent, buyer, "3BR Bole")
    assert res.reply.startswith("Here is what I found:") and "1. Noah Bole Towers 3BR Apartment" in res.reply
    assert "compare them or book a visit" in res.reply


async def test_widening_is_stated_even_when_the_model_cannot_write_the_reply(spy_repo, vector_of):
    class BrokenWriter(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            if schema is Recommendation:
                raise RuntimeError("down")
            return await super().generate_structured(prompt, schema, system=system)

    gw = BrokenWriter([ext(max_budget_etb=16_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole")],
                      vector=await vector_of("Gift Bole Court%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "widenfallback")
    res = await say_to(agent, buyer, "3 bedroom in Bole under 16M")
    assert "widened the search" in res.reply and "17.92M ETB" in res.reply


async def test_option_numbers_in_the_reply_always_match_the_stored_order(spy_repo, db_conn, vector_of):
    """Regression: a live model once listed the options in a different order than the agent stored them,
    so 'book the first one' would have booked the wrong property."""
    class Reorders(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            if schema is Recommendation:
                return Recommendation(intro="INTRO", blurbs=["second first", "first second"], closing="CLOSING")
            return await super().generate_structured(prompt, schema, system=system)

    gw = Reorders([ext(**BOLE_3BR)], vector=await vector_of("Gift Bole Court%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "order")
    res = await say_to(agent, buyer, "3BR Bole")
    stored = [m["title"] for m in res.state["active_matches"]]
    shown = [line.split(" - ")[0].split(". ", 1)[1] for line in res.reply.splitlines() if line[:2] in ("1.", "2.", "3.")]
    assert shown == stored and len(stored) >= 2


async def test_a_blurb_count_mismatch_uses_the_plain_layout_instead_of_misaligning_text(spy_repo, vector_of):
    class WrongCount(FakeGateway):
        async def generate_structured(self, prompt, schema, *, system=None):
            if schema is Recommendation:
                return Recommendation(intro="INTRO", blurbs=["only one"], closing="CLOSING")
            return await super().generate_structured(prompt, schema, system=system)

    gw = WrongCount([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "count")
    res = await say_to(agent, buyer, "3BR Bole")
    assert res.reply.startswith("Here is what I found:") and "only one" not in res.reply


# ---------------- comparison ----------------

async def test_comparison_numbers_are_computed_in_code(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("compare", referenced_options=[1, 2])], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "compare")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "compare the first two")
    m = res.state["comparison_matrix"]["Noah Bole Towers 3BR Apartment"]
    assert m["developer"] == "Noah Real Estate" and m["price_per_sqm_etb"] == round(19_800_000 / 135)
    assert m["down_payment_etb"] == round(19_800_000 * 0.30) and m["est_rental_yield_pct"] == 7.0
    assert set(res.state["comparison_matrix"]) == {"Noah Bole Towers 3BR Apartment", "Gift Bole Court 3BR Apartment"}


async def test_comparison_with_nothing_to_compare_asks_for_criteria(spy_repo):
    agent, buyer = make_agent(spy_repo, FakeGateway([ext("compare")])), await new_buyer(spy_repo, "emptycompare")
    assert "budget" in (await say_to(agent, buyer, "compare them")).reply


# ---------------- booking ----------------

BOOK = dict(full_name="Abel Tesfaye", phone="+251911000000", email="abel@buyer.test", referenced_options=[1, 2],
            tour_start_iso="2026-11-03T10:00:00+03:00")


async def book_two_options(spy_repo, vector_of, tag, **settings):
    email = MockEmailService()
    services = make_services(email=email)
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services, **settings), await new_buyer(spy_repo, tag)
    await say_to(agent, buyer, "3BR Bole")
    return agent, buyer, services, email, await say_to(agent, buyer, "book 1 and 2 tomorrow at 10")


async def test_booking_creates_tour_calendar_event_ledger_and_emails(spy_repo, db_conn, vector_of):
    agent, buyer, services, email, res = await book_two_options(spy_repo, vector_of, "book")

    assert res.state["booked"] and "booked for" in res.reply and "Tue 03 Nov, 10:00" in res.reply
    assert len(services.calendar.events) == 1
    cur = await db_conn.execute("select id, mapbox_route_json from tours_and_appointments where buyer_id=%s", (buyer["id"],))
    tour = await cur.fetchone()
    assert set(tour["mapbox_route_json"]) == {"geometry", "distance_meters", "duration_seconds", "bbox"}
    stops = await spy_repo.get_tour_stops(tour["id"])
    assert [s["stop_order"] for s in stops] == [1, 2] and len(stops) == 2

    ledger = await spy_repo.list_ledger("LEAD_REGISTERED")
    mine = [r for r in ledger if r["buyer_id"] == buyer["id"]]
    assert len(mine) == 2 and len({r["developer_id"] for r in mine}) == 2
    assert {r["lead_reg_email_id"] for r in mine} == {"mock-email-1", "mock-email-2"}

    recipients = [m["to"] for m in email.sent]
    assert sorted(recipients[:2]) == ["sales@gift.example", "sales@noah.example"] and recipients[2] == "abel@buyer.test"
    notice = next(m for m in email.sent if m["to"] == "sales@noah.example")
    assert "Abel Tesfaye" in notice["html"] and "+251911000000" in notice["html"] and "Ermias Desalegn" in notice["html"]
    assert notice["idempotency_key"].startswith("lead-")

    row = await buyer_row(db_conn, buyer)
    assert row["deal_stage"] == "SITE_VISIT_BOOKED" and row["human_takeover"] is False


async def test_a_single_stop_visit_does_not_report_driving_time(spy_repo, vector_of):
    """Regression found in the dashboard: 'About 0.0 km of driving, roughly 0 minutes between stops'."""
    email = MockEmailService()
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **(BOOK | {"referenced_options": [1]}))], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=make_services(email=email)), await new_buyer(spy_repo, "onestop")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book the first")
    assert res.state["booked"] and "booked for" in res.reply
    assert "km of driving" not in res.reply
    buyer_mail = next(m for m in email.sent if m["to"] == "abel@buyer.test")
    assert "Total driving" not in buyer_mail["html"]


async def test_an_unreachable_calendar_means_no_booking_and_a_clear_message(spy_repo, db_conn, vector_of):
    from app.services.calendar import CalendarError, MockCalendarService

    class Down(MockCalendarService):
        async def is_available(self, start, end):
            raise CalendarError("Google Calendar returned 503")

        async def find_slots(self, **kw):
            raise CalendarError("Google Calendar returned 503")

    services = make_services()
    services.calendar = Down()
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "caldown")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book both")
    assert not res.state["booked"] and "can't reach the broker's calendar" in res.reply
    assert (await spy_repo.list_ledger()) == [] or all(r["buyer_id"] != buyer["id"] for r in await spy_repo.list_ledger())
    cur = await db_conn.execute("select count(*) n from tours_and_appointments where buyer_id=%s", (buyer["id"],))
    assert (await cur.fetchone())["n"] == 0


async def test_tours_start_from_the_office_when_one_is_configured(spy_repo, vector_of):
    seen = {}

    class Spy(MockRoutingService):
        async def plan_route(self, stops, *, origin=None):
            seen["origin"] = origin
            return await super().plan_route(stops, origin=origin)

    services = make_services()
    services.routing = Spy()
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services, office_lat=9.0, office_lng=38.75), await new_buyer(spy_repo, "office")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book both")
    assert seen["origin"] == (9.0, 38.75) and res.state["booked"]
    assert "km of driving in total" in res.reply


async def test_missing_contact_details_are_requested_before_booking(spy_repo, db_conn, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", referenced_options=[1], tour_start_iso="2026-11-03T10:00:00+03:00")],
                     vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "nophone")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book the first")
    assert "full name and phone number" in res.reply and not res.state["booked"]
    cur = await db_conn.execute("select count(*) n from tours_and_appointments where buyer_id=%s", (buyer["id"],))
    assert (await cur.fetchone())["n"] == 0


async def test_ambiguous_booking_asks_which_option(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", full_name="A", phone="1")], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "which")
    await say_to(agent, buyer, "3BR Bole")
    assert "Which of the options" in (await say_to(agent, buyer, "I want to visit")).reply


async def test_no_requested_time_offers_open_slots(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", full_name="A", phone="1", referenced_options=[1])], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "slots")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "I'd like to visit the first")
    assert "When would you like to go?" in res.reply and res.reply.count("- ") == 3 and not res.state["booked"]


@pytest.mark.parametrize("when", ["2026-11-01T10:00:00+03:00", "2026-11-03T20:00:00+03:00", "2026-11-02T08:30:00+03:00"])
async def test_unavailable_times_get_alternatives(spy_repo, vector_of, when):
    """Sunday, after closing, and less than an hour from now."""
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", full_name="A", phone="1", referenced_options=[1], tour_start_iso=when)],
                     vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "unavail")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "visit the first then")
    assert "isn't free" in res.reply and "I can offer" in res.reply and not res.state["booked"]


async def test_second_buyer_cannot_take_an_already_booked_slot(spy_repo, vector_of):
    services = make_services()
    vec = await vector_of("Noah Bole Towers 3BR%")
    one = BOOK | {"referenced_options": [1]}
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **one), ext(**BOLE_3BR), ext("book_tour", **one)], vector=vec)
    first, second = await new_buyer(spy_repo, "first"), await new_buyer(spy_repo, "second")
    agent = make_agent(spy_repo, gw, services=services)
    await say_to(agent, first, "3BR Bole")
    assert (await say_to(agent, first, "book")).state["booked"]
    await say_to(agent, second, "3BR Bole")
    res = await say_to(agent, second, "book the same time")
    assert not res.state["booked"] and "isn't free" in res.reply and len(services.calendar.events) == 1


async def test_a_failed_lead_notice_is_recorded_but_does_not_block_the_booking(spy_repo, vector_of):
    class FlakyEmail(MockEmailService):
        async def send(self, *, to, subject, html, idempotency_key=None):
            if "noah" in to:
                raise RuntimeError("smtp down")
            return await super().send(to=to, subject=subject, html=html, idempotency_key=idempotency_key)

    services = make_services(email=FlakyEmail())
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK)], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "flaky")
    await say_to(agent, buyer, "3BR Bole")
    res = await say_to(agent, buyer, "book both")
    assert res.state["booked"] and res.state["lead_reg_failed"] == ["noah"]
    rows = {r["developer_name"]: r for r in await spy_repo.list_ledger() if r["buyer_id"] == buyer["id"]}
    assert rows["Noah Real Estate"]["lead_reg_email_id"] is None and rows["Gift Real Estate"]["lead_reg_email_id"]


async def test_high_value_booking_hands_the_chat_to_the_broker(spy_repo, db_conn, vector_of):
    services = make_services()
    gw = FakeGateway(
        [ext(max_budget_etb=50_000_000, min_bedrooms=4, preferred_sub_cities=["Bole"], search_query="penthouse"),
         ext("book_tour", full_name="Big Buyer", phone="+251911999999", tour_start_iso="2026-11-03T10:00:00+03:00")],
        vector=await vector_of("Noah Bole Penthouse%"),
    )
    agent, buyer = make_agent(spy_repo, gw, services=services, demo_fallback_telegram_chat_id="999"), await new_buyer(spy_repo, "whale")
    await say_to(agent, buyer, "penthouse in Bole")
    res = await say_to(agent, buyer, "book it")
    assert res.state["booked"] and "take over this chat" in res.reply
    assert (await buyer_row(db_conn, buyer))["human_takeover"] is True
    assert services.telegram.messages[0]["chat_id"] == "999" and "Big Buyer" in services.telegram.messages[0]["text"]
