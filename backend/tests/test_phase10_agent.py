"""Phase 10 behaviour through the real graph: buttons read in code, option details, consultations, objections, brochure and dossier."""

import pytest

from app.agent.nodes import quick_extraction
from app.services.email import MockEmailService
from tests.agent_helpers import NOW, FakeGateway, ext, make_agent, make_services, new_buyer, say_to
from tests.test_agent_graph import BOLE_3BR

CONTACT = dict(full_name="Abel Tesfaye", phone="+251911000000", email="abel@buyer.test")


# ---------------- reading a button tap without a model ----------------

@pytest.mark.parametrize("text,intent,options,start", [
    ("Tell me more about option 2", "details", [2], None),
    ("tell me more about option 1.", "details", [1], None),
    ("Book a site visit for option 3", "book_tour", [3], None),
    ("I'd like to book a site visit for option 1.", "book_tour", [1], None),
    ("Compare these options", "compare", [], None),
    ("Compare these options for me.", "compare", [], None),
    ("I'd like a phone consultation", "book_consultation", [], None),
    ("Book Tue 03 Nov, 10:00", "book_tour", [], "2026-11-03T10:00:00+03:00"),
    ("Book Fri 6 Nov, 9:30", "book_tour", [], "2026-11-06T09:30:00+03:00"),
    ("Book call Tue 03 Nov, 15:00", "book_consultation", [], "2026-11-03T15:00:00+03:00"),
])
def test_button_sentences_are_understood_in_code(text, intent, options, start):
    ex = quick_extraction(text, 3, NOW)
    assert ex is not None and ex.intent == intent and ex.referenced_options == options and ex.tour_start_iso == start


@pytest.mark.parametrize("text,count", [
    ("Tell me more about option 4", 3), ("Book a site visit for option 0", 3), ("Compare these options", 1), ("Compare these options", 0),
    ("Book Sat 31 Feb, 10:00", 3), ("I have 15M and want a 2BR", 3), ("Tell me more about option 2 and also the price", 3), ("", 3),
])
def test_anything_that_is_not_a_valid_button_goes_to_the_model(text, count):
    assert quick_extraction(text, count, NOW) is None


def test_an_offer_for_a_month_already_past_means_next_year():
    january = NOW.replace(year=2026, month=12, day=30)
    ex = quick_extraction("Book Fri 8 Jan, 10:00", 0, january)
    assert ex.tour_start_iso.startswith("2027-01-08T10:00")


def test_the_model_is_not_called_for_a_button_tap():
    ex = quick_extraction("Tell me more about option 1", 2, NOW)
    assert ex.max_budget_etb is None and ex.objections == [] and ex.preferred_sub_cities is None, "a tap never changes the buyer's stated needs"


# ---------------- buttons under the recommendation ----------------

async def search(spy_repo, vector_of, tag, extractions=(), **settings):
    gw = FakeGateway([ext(**BOLE_3BR), *extractions], vector=await vector_of("Noah Bole Towers 3BR%"))
    services = make_services(email=MockEmailService())
    agent, buyer = make_agent(spy_repo, gw, services=services, **settings), await new_buyer(spy_repo, tag)
    res = await say_to(agent, buyer, "3BR in Bole")
    return agent, buyer, gw, services, res


async def test_recommendations_come_with_a_button_row_per_option_and_a_compare_button(spy_repo, vector_of):
    *_, res = await search(spy_repo, vector_of, "p10-buttons")
    n = len(res.state["active_matches"])
    rows = res.state["quick_replies"]
    assert n >= 2 and len(rows) == n + 2
    assert rows[0] == [{"label": "Details 1", "text": "Tell me more about option 1"}, {"label": "Book 1", "text": "Book a site visit for option 1"}]
    assert rows[n] == [{"label": "Compare all", "text": "Compare these options"}]
    assert rows[-1] == [{"label": "What can I afford?", "text": "What can I afford?"}, {"label": "Phone consultation", "text": "I'd like a phone consultation"}]
    assert all(len(b["text"].encode()) <= 64 for row in rows for b in row), "Telegram limits button data to 64 bytes"


async def test_option_details_are_stored_facts_with_no_model_call(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(spy_repo, vector_of, "p10-details")
    calls_before = len(gw.calls)
    first = res.state["active_matches"][0]["title"]
    out = await say_to(agent, buyer, "Tell me more about option 1")
    assert [k for k, _ in gw.calls[calls_before:]] == [], "reading the tap and writing the details need no model"
    assert first in out.reply and "Payment plan:" in out.reply and "on signing" in out.reply and "a month on average" in out.reply
    assert "Construction:" in out.reply and "Features:" in out.reply
    assert out.state["quick_replies"][0][0]["text"] == "Book a site visit for option 1"
    assert out.state["active_matches"] == res.state["active_matches"], "looking at an option does not change what 'option 1' means"


async def test_details_without_a_selection_asks_which_option(spy_repo, vector_of):
    agent, buyer = make_agent(spy_repo, FakeGateway([ext("details")])), await new_buyer(spy_repo, "p10-detailsask")
    assert "Which option" in (await say_to(agent, buyer, "tell me more")).reply


async def test_booking_from_the_button_asks_for_contact_details_first(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(spy_repo, vector_of, "p10-bookbtn")
    before = gw.count("structured")
    out = await say_to(agent, buyer, "Book a site visit for option 1")
    assert "full name and phone number" in out.reply and gw.count("structured") == before


async def test_a_slot_offer_is_a_set_of_buttons_and_tapping_one_books_it(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(
        spy_repo, vector_of, "p10-slots", [ext("book_tour", referenced_options=[1], **CONTACT)])
    offer = await say_to(agent, buyer, "I'd like to visit the first one")
    assert "When would you like to go?" in offer.reply and len(offer.state["quick_replies"]) == 3
    tap = offer.state["quick_replies"][1][0]
    assert tap["text"] == f"Book {tap['label']}" and offer.state["pending_booking"]["ids"] == [offer.state["active_matches"][0]["id"]]
    booked = await say_to(agent, buyer, tap["text"])
    assert booked.state["booked"] and tap["label"] in booked.reply and len(services.calendar.events) == 1
    assert booked.state["pending_booking"] is None, "nothing is left waiting once the visit is booked"
    stops = await spy_repo.get_tour_stops((await spy_repo.list_tours())[0]["id"])
    assert [str(s["property_id"]) for s in stops] == [offer.state["active_matches"][0]["id"]], "the tapped time booked the option chosen earlier"


async def test_a_consultation_slot_tap_books_a_call_not_a_visit(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(spy_repo, vector_of, "p10-callslot", [ext("book_consultation", **CONTACT)])
    offer = await say_to(agent, buyer, "I'd like a call")
    tap = offer.state["quick_replies"][0][0]
    assert tap["text"].startswith("Book call ")
    booked = await say_to(agent, buyer, tap["text"])
    assert booked.state["booked"] and "will phone you" in booked.reply
    assert next(iter(services.calendar.events.values()))["summary"].startswith("Consultation call")


# ---------------- what the buyer worried about ----------------

async def test_objections_are_remembered_once_for_the_dossier(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(spy_repo, vector_of, "p10-obj", [
        ext("small_talk", objections=["price feels high", "worried about delivery delays"]), ext("small_talk", objections=["Price feels high"])])
    await say_to(agent, buyer, "hmm, it is expensive and I fear delays")
    await say_to(agent, buyer, "yes the price")
    saved = await spy_repo.get_buyer(buyer["id"])
    assert [o["text"] for o in saved["objections"]] == ["price feels high", "worried about delivery delays"]
    assert all(o["at"] for o in saved["objections"])


async def test_only_the_most_recent_objections_are_kept(spy_repo):
    buyer = await new_buyer(spy_repo, "p10-objcap")
    await spy_repo.add_objections(buyer["id"], [f"worry {i}" for i in range(30)], keep=20)
    kept = [o["text"] for o in (await spy_repo.get_buyer(buyer["id"]))["objections"]]
    assert kept[0] == "worry 10" and kept[-1] == "worry 29" and len(kept) == 20


# ---------------- phone consultation ----------------

async def test_a_consultation_is_a_calendar_event_and_a_tour_without_stops_or_developer_notices(spy_repo, db_conn, vector_of):
    agent, buyer, gw, services, res = await search(
        spy_repo, vector_of, "p10-consult",
        [ext("book_consultation", tour_start_iso="2026-11-03T15:00:00+03:00", **CONTACT)], broker_telegram_chat_id="555")
    out = await say_to(agent, buyer, "can the broker call me tomorrow at 3pm?")
    assert out.state["booked"] and "will phone you on +251911000000" in out.reply and "Tue 03 Nov, 15:00" in out.reply

    event = next(iter(services.calendar.events.values()))
    assert event["summary"].startswith("Consultation call") and event["location"] == "Phone"
    assert (event["end"] - event["start"]).total_seconds() == 30 * 60

    tour = await (await db_conn.execute("select * from tours_and_appointments where buyer_id = %s", (buyer["id"],))).fetchone()
    assert tour["kind"] == "CONSULTATION" and tour["mapbox_route_json"] is None
    assert await spy_repo.get_tour_stops(tour["id"]) == []
    assert not [r for r in await spy_repo.list_ledger("LEAD_REGISTERED") if r["buyer_id"] == buyer["id"]], "no developer was told about a phone call"
    assert [m["to"] for m in services.email.sent if "sales@" in m["to"]] == []
    assert any(m["to"] == "abel@buyer.test" and "consultation call" in m["subject"].lower() for m in services.email.sent)
    assert (await spy_repo.get_buyer(buyer["id"]))["deal_stage"] != "SITE_VISIT_BOOKED"


async def test_a_consultation_asks_for_missing_contact_details(spy_repo, vector_of):
    agent, buyer, *_ = await search(spy_repo, vector_of, "p10-consult-contact", [ext("book_consultation")])
    out = await say_to(agent, buyer, "please call me")
    assert "full name and phone number" in out.reply and not out.state.get("booked")


async def test_a_consultation_without_a_time_offers_buttons(spy_repo, vector_of):
    agent, buyer, *_ = await search(spy_repo, vector_of, "p10-consult-slots", [ext("book_consultation", **CONTACT)])
    out = await say_to(agent, buyer, "I'd like a call")
    assert "good time for the call" in out.reply and len(out.state["quick_replies"]) == 3


async def test_a_consultation_button_works_without_a_search_first(spy_repo):
    agent, buyer = make_agent(spy_repo, FakeGateway([])), await new_buyer(spy_repo, "p10-consult-btn")
    out = await say_to(agent, buyer, "I'd like a phone consultation")
    assert "full name and phone number" in out.reply


# ---------------- brochure and dossier with a site visit ----------------

async def visit(spy_repo, vector_of, tag, **settings):
    agent, buyer, gw, services, res = await search(
        spy_repo, vector_of, tag,
        [ext("book_tour", referenced_options=[1, 2], tour_start_iso="2026-11-03T10:00:00+03:00", **CONTACT)], broker_telegram_chat_id="555", **settings)
    return agent, buyer, services, await say_to(agent, buyer, "book 1 and 2 tomorrow at 10")


async def test_the_itinerary_email_carries_the_brochure_inline_and_as_a_pdf(spy_repo, vector_of):
    agent, buyer, services, out = await visit(spy_repo, vector_of, "p10-brochure")
    assert out.state["booked"]
    mail = next(m for m in services.email.sent if m["to"] == "abel@buyer.test" and "tour is confirmed" in m["subject"])
    assert "Your property comparison" in mail["html"] and "Payment plan" in mail["html"] and "Average per month" in mail["html"]
    [pdf] = mail["attachments"]
    assert pdf.filename == "property-comparison.pdf" and pdf.content.startswith(b"%PDF")


async def test_the_brochure_is_also_sent_as_a_document_on_telegram(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", referenced_options=[1, 2], tour_start_iso="2026-11-03T10:00:00+03:00", **CONTACT)],
                     vector=await vector_of("Noah Bole Towers 3BR%"))
    services = make_services(email=MockEmailService())
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "p10-tgdoc")
    await agent.handle(buyer_id=str(buyer["id"]), thread_id=buyer["thread_id"], inbound={"kind": "text", "text": "3BR in Bole"}, chat_id=777)
    await agent.handle(buyer_id=str(buyer["id"]), thread_id=buyer["thread_id"], inbound={"kind": "text", "text": "book both"}, chat_id=777)
    [doc] = services.telegram.documents
    assert doc["chat_id"] == 777 and doc["filename"] == "property-comparison.pdf" and doc["content"].startswith(b"%PDF")


async def test_the_broker_gets_the_dossier_before_the_tour(spy_repo, db_conn, vector_of):
    agent, buyer, services, out = await visit(spy_repo, vector_of, "p10-dossier")
    sent = [m for m in services.telegram.messages if m["chat_id"] == "555" and "PRE-TOUR DOSSIER" in m["text"]]
    assert len(sent) == 1
    text = sent[0]["text"]
    assert "Abel Tesfaye" in text and "Units:" in text and "Commission about" in text and "Route:" in text
    tour = await (await db_conn.execute("select dossier_sent_at from tours_and_appointments where buyer_id = %s", (buyer["id"],))).fetchone()
    assert tour["dossier_sent_at"] is not None


async def test_a_failing_brochure_or_dossier_never_undoes_the_booking(spy_repo, vector_of, monkeypatch):
    import app.agent.nodes as nodes

    def boom(*a, **k):
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(nodes, "brochure_pdf", boom)
    monkeypatch.setattr(nodes, "deliver_dossier", boom)
    agent, buyer, services, out = await visit(spy_repo, vector_of, "p10-resilient")
    assert out.state["booked"] and len(services.calendar.events) == 1
    mail = next(m for m in services.email.sent if m["to"] == "abel@buyer.test")
    assert mail["attachments"] == [] and "Your property comparison" in mail["html"], "the inline brochure still goes out"


# ---------------- comparison now includes payment plans ----------------

async def test_the_comparison_includes_payment_plans_and_developer_reliability(spy_repo, vector_of):
    agent, buyer, gw, services, res = await search(spy_repo, vector_of, "p10-compare", [ext("compare")])
    out = await say_to(agent, buyer, "compare the options")
    matrix = out.state["comparison_matrix"]
    assert len(matrix) >= 2
    for row in matrix.values():
        assert "on signing" in row["payment_plan"] and row["monthly_equivalent_etb"] > 0 and 0 < row["reliability_score"] <= 1
    prompt = gw.prompts("text")[-1]
    assert "payment plan" in prompt and "reliability score" in prompt
