"""Proactive jobs on the real database (rolled back), with mock Telegram and email."""
import itertools
from datetime import UTC, date, datetime, timedelta

import pytest

from app.repository import LedgerEntry
from app.services.email import MockEmailService
from app.services.telegram import MockTelegramService, TelegramError
from tests.agent_helpers import TZ, make_proactive, make_services

NOAH = "Noah Bole Towers 3BR%"
GIFT = "Gift Bole Court%"
_telegram_ids = itertools.count(880001)

pytestmark = pytest.mark.usefixtures("quiet_world")


async def pid(db_conn, like):
    cur = await db_conn.execute("select id from properties where title like %s", (like,))
    return (await cur.fetchone())["id"]


async def make_buyer(repo, db_conn, tag, *, like=NOAH, budget=25_000_000, beds=3, cities=("Bole",), stage="QUALIFIED",
                     telegram=True, email=None, name="Abel Tesfaye", takeover=False):
    buyer = await repo.get_or_create_buyer(f"test:{tag}", next(_telegram_ids) if telegram else None)
    await db_conn.execute(
        "update buyers_crm set max_budget_etb=%s, min_bedrooms=%s, preferred_sub_cities=%s, deal_stage=%s, full_name=%s, email=%s, "
        "human_takeover=%s, preference_embedding=(select embedding from properties where title like %s limit 1) where id=%s",
        (budget, beds, list(cities), stage, name, email, takeover, like, buyer["id"]),
    )
    return await repo.get_buyer(buyer["id"])


async def clone_property(db_conn, like, title, price):
    """A copy of a seeded unit that counts as real market news (the seed rows are deliberately ignored)."""
    cur = await db_conn.execute(
        "insert into properties (developer_id, title, project_name, unit_type, sub_city, lat, lng, price_etb, bedrooms, sqm, "
        "down_payment_pct, delivery_date, description, vision_amenities, content_hash, embedding) "
        "select developer_id, %s, project_name, unit_type, sub_city, lat, lng, %s, bedrooms, sqm, down_payment_pct, delivery_date, "
        "description, '{}'::jsonb, md5(%s), embedding from properties where title like %s limit 1 returning id",
        (title, price, title, like),
    )
    return (await cur.fetchone())["id"]


async def memory(agent, buyer):
    return (await agent.graph.aget_state({"configurable": {"thread_id": buyer["thread_id"]}})).values


class Down(MockTelegramService):
    async def send_message(self, *a, **k):
        raise TelegramError("blocked by user")


# ---------------- A: reverse-RAG alerts ----------------

async def test_price_drop_alerts_only_the_buyers_it_suits(repo, db_conn):
    proactive, services, agent = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "abel")
    too_poor = await make_buyer(repo, db_conn, "poor", budget=15_000_000, name="Poor")
    not_started = await make_buyer(repo, db_conn, "new", stage="NEW", name="Newcomer")
    wrong_area = await make_buyer(repo, db_conn, "ayat", cities=("Ayat",), name="Elsewhere")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)

    out = await proactive.reverse_rag_alerts()
    assert (out["sent"], out["failed"]) == (1, 0)
    assert [m["chat_id"] for m in services.telegram.messages] == [abel["telegram_id"]]
    text = services.telegram.messages[0]["text"]
    assert text.startswith("Hi Abel,") and "dropped from 19.8M ETB to 17.5M ETB (11.6% lower)" in text and "25M ETB budget" in text

    assert [r["role"] for r in await repo.list_messages(abel["id"])] == ["agent"]
    state = await memory(agent, abel)
    assert [m["title"] for m in state["active_matches"]] == ["Noah Bole Towers 3BR Apartment"], "'book the first one' must work"
    assert state["messages"][-1].content == text
    for silent in (too_poor, not_started, wrong_area):
        assert await repo.list_messages(silent["id"]) == []


async def test_running_the_job_again_never_repeats_a_message(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    assert (await proactive.reverse_rag_alerts())["sent"] == 1
    again = await proactive.reverse_rag_alerts()
    assert again["sent"] == 0 and again["skipped"] >= 1 and len(services.telegram.messages) == 1


async def test_dry_run_reports_without_sending_or_reserving(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "abel")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts(dry_run=True)
    assert out["dry_run"] and out["sent"] == 0 and out["details"][0]["would_send"] == ["Noah Bole Towers 3BR Apartment"]
    assert services.telegram.messages == [] and await repo.list_events() == []
    assert (await proactive.reverse_rag_alerts())["sent"] == 1, "a dry run must not use up the real alert"


async def test_several_updates_become_one_numbered_message_matching_the_agents_memory(repo, db_conn):
    proactive, services, agent = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "abel")
    await repo.update_property_price(await pid(db_conn, NOAH), 18_000_000)
    await repo.update_property_price(await pid(db_conn, GIFT), 15_000_000)

    assert (await proactive.reverse_rag_alerts())["sent"] == 1
    assert len(services.telegram.messages) == 1
    text = services.telegram.messages[0]["text"]
    assert "2 updates" in text and text.index("1. Noah Bole Towers") < text.index("2. Gift Bole Court")
    titles = [m["title"] for m in (await memory(agent, abel))["active_matches"]]
    assert titles == ["Noah Bole Towers 3BR Apartment", "Gift Bole Court 3BR Apartment"], "numbering must match what was stored"


async def test_a_buyer_is_alerted_at_most_once_a_day(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    await repo.update_property_price(await pid(db_conn, NOAH), 18_000_000)
    assert (await proactive.reverse_rag_alerts())["sent"] == 1
    await repo.update_property_price(await pid(db_conn, GIFT), 15_000_000)  # a different drop, same day
    second = await proactive.reverse_rag_alerts()
    assert second["sent"] == 0 and len(services.telegram.messages) == 1


async def test_a_chat_the_broker_has_taken_over_is_not_messaged_by_the_bot(repo, db_conn):
    proactive, services, _ = make_proactive(repo, broker_telegram_chat_id="999")
    held = await make_buyer(repo, db_conn, "held", takeover=True)
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts()
    assert out["sent"] == 1
    assert [m["chat_id"] for m in services.telegram.messages] == ["999"], "only the broker is told"
    assert "you have this chat" in services.telegram.messages[0]["text"] and await repo.list_messages(held["id"]) == []
    feed = await repo.list_events(audience="broker")
    assert [e["kind"] for e in feed] == ["TAKEOVER_MATCH"]
    assert (await proactive.reverse_rag_alerts())["sent"] == 0, "and only once"


async def test_web_chat_buyers_get_the_alert_in_their_transcript(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    web = await make_buyer(repo, db_conn, "web", telegram=False)
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts()
    assert out["sent"] == 1 and services.telegram.messages == []
    assert (await repo.list_messages(web["id"]))[0]["content"].startswith("Hi Abel")
    assert (await repo.list_events())[0]["channel"] == "web"


async def test_buyers_with_an_email_get_it_too(repo, db_conn):
    email = MockEmailService()
    proactive, services, _ = make_proactive(repo, services=make_services(email=email))
    await make_buyer(repo, db_conn, "mail", email="abel@buyer.test")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    await proactive.reverse_rag_alerts()
    assert [m["to"] for m in email.sent] == ["abel@buyer.test"] and email.sent[0]["subject"].startswith("Price drop")
    assert (await repo.list_events())[0]["channel"] == "telegram+email"


async def test_a_failed_delivery_is_retried_on_the_next_run(repo, db_conn):
    services = make_services()
    services.telegram = Down()
    proactive, _, _ = make_proactive(repo, services=services)
    await make_buyer(repo, db_conn, "abel")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts()
    assert out["failed"] == 1 and out["sent"] == 0 and await repo.list_events() == []

    services.telegram = MockTelegramService()
    assert (await proactive.reverse_rag_alerts())["sent"] == 1 and len(services.telegram.messages) == 1


async def test_a_new_listing_is_announced_but_seed_inventory_never_is(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    assert (await proactive.reverse_rag_alerts())["sent"] == 0, "seed rows were created today but are not news"

    await clone_property(db_conn, NOAH, "Noah Bole Towers 3BR Apartment (Block B)", 19_000_000)
    out = await proactive.reverse_rag_alerts()
    assert out["sent"] == 1
    assert "a new listing matches" in services.telegram.messages[0]["text"] and "Block B" in services.telegram.messages[0]["text"]


async def test_buyers_who_already_have_a_deal_or_no_preferences_are_left_alone(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    for stage in ("CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID", "LOST", "NEW"):
        await make_buyer(repo, db_conn, f"stage-{stage}", stage=stage)
    no_vector = await make_buyer(repo, db_conn, "novec")
    await db_conn.execute("update buyers_crm set preference_embedding = null where id = %s", (no_vector["id"],))
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    out = await proactive.reverse_rag_alerts()
    assert out["sent"] == 0 and services.telegram.messages == []


async def test_a_price_increase_or_unchanged_price_alerts_nobody(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    same = await repo.update_property_price(await pid(db_conn, NOAH), 19_800_000)
    up = await repo.update_property_price(await pid(db_conn, NOAH), 21_000_000)
    assert same["changed"] is False and up["change_pct"] > 0
    assert (await proactive.reverse_rag_alerts())["sent"] == 0


async def test_a_drop_that_has_since_been_reversed_is_not_announced(repo, db_conn):
    """Regression: after a live demo the drop stayed in the 24 h history although the price was back to normal."""
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    unit = await pid(db_conn, NOAH)
    await repo.update_property_price(unit, 17_500_000)
    await repo.update_property_price(unit, 19_800_000)  # back to the original price
    assert (await proactive.reverse_rag_alerts())["sent"] == 0 and services.telegram.messages == []


async def test_only_the_latest_drop_is_announced_and_with_the_current_price(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    await make_buyer(repo, db_conn, "abel")
    unit = await pid(db_conn, NOAH)
    await repo.update_property_price(unit, 18_500_000)
    await repo.update_property_price(unit, 17_500_000)
    assert (await proactive.reverse_rag_alerts())["sent"] == 1
    text = services.telegram.messages[0]["text"]
    assert "dropped from 18.5M ETB to 17.5M ETB" in text and "19.8M" not in text


async def test_reverse_match_function_applies_every_filter(db_conn):
    pid_ = await pid(db_conn, NOAH)
    async def ids(**changes):
        await db_conn.execute("update buyers_crm set preference_embedding = (select embedding from properties where id=%s)", (pid_,))
        cur = await db_conn.execute("select count(*) n from match_buyers_for_property(%s, 0.64, 50)", (pid_,))
        return (await cur.fetchone())["n"]

    cur = await db_conn.execute("insert into buyers_crm (thread_id, max_budget_etb, min_bedrooms, preferred_sub_cities, deal_stage) "
                                "values ('test:fn', 25000000, 3, '{Bole}', 'QUALIFIED') returning id")
    buyer = (await cur.fetchone())["id"]
    assert await ids() == 1
    for sql, expected_zero in [
        ("update buyers_crm set max_budget_etb = 1000000 where id = %s", True),
        ("update buyers_crm set max_budget_etb = 25000000, min_bedrooms = 4 where id = %s", True),
        ("update buyers_crm set min_bedrooms = 3, preferred_sub_cities = '{Ayat}' where id = %s", True),
        ("update buyers_crm set preferred_sub_cities = '{}' where id = %s", False),
        ("update buyers_crm set deal_stage = 'LOST' where id = %s", True),
    ]:
        await db_conn.execute(sql, (buyer,))
        assert (await ids() == 0) is expected_zero, sql


# ---------------- B: tour reminders ----------------

async def book(repo, db_conn, buyer, start, *like):
    return await repo.create_tour(buyer_id=buyer["id"], scheduled_start=start, stops=[await pid(db_conn, l) for l in like],
                                  route_json=None, calendar_event_id=None)


async def test_reminder_for_a_visit_within_24_hours(repo, db_conn):
    proactive, services, agent = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "tour")
    tour = await book(repo, db_conn, abel, datetime.now(UTC) + timedelta(hours=20), NOAH, GIFT)

    out = await proactive.tour_reminders()
    assert out["sent"] == 1
    text = services.telegram.messages[0]["text"]
    assert text.startswith("Hi Abel,") and "site visit with Ermias Desalegn" in text and "1. Noah Bole Towers" in text and "2. Gift Bole Court" in text
    assert "https://www.google.com/maps/search/?api=1&query=" in text
    assert len(services.telegram.locations) == 1 and services.telegram.locations[0][0] == abel["telegram_id"]
    cur = await db_conn.execute("select reminder_sent from tours_and_appointments where id=%s", (tour["id"],))
    assert (await cur.fetchone())["reminder_sent"] is True
    again = await proactive.tour_reminders()
    assert again["sent"] == 0 and len(services.telegram.messages) == 1


async def test_only_visits_inside_the_window_are_reminded(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "windows")
    now = datetime.now(UTC)
    await book(repo, db_conn, abel, now + timedelta(hours=30), NOAH)   # too far away
    await book(repo, db_conn, abel, now - timedelta(hours=2), GIFT)    # already happened
    assert (await proactive.tour_reminders())["sent"] == 0 and services.telegram.messages == []


async def test_reminder_wording_says_today_or_tomorrow():
    from app import proactive_messages as m

    now = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)
    buyer, stops = {"full_name": "Abel"}, [{"title": "X", "lat": 9.0, "lng": 38.7}]
    same_day = m.tour_reminder_text(buyer=buyer, start=datetime(2026, 11, 2, 15, 0, tzinfo=TZ), now=now, stops=stops, broker="B", tz="Africa/Addis_Ababa")
    next_day = m.tour_reminder_text(buyer=buyer, start=datetime(2026, 11, 3, 7, 0, tzinfo=TZ), now=now, stops=stops, broker="B", tz="Africa/Addis_Ababa")
    assert "is today at 15:00" in same_day and "is tomorrow at 07:00" in next_day


async def test_a_failed_reminder_leaves_the_visit_to_be_retried(repo, db_conn):
    services = make_services()
    services.telegram = Down()
    proactive, _, _ = make_proactive(repo, services=services)
    abel = await make_buyer(repo, db_conn, "retry")
    tour = await book(repo, db_conn, abel, datetime.now(UTC) + timedelta(hours=10), NOAH)
    assert (await proactive.tour_reminders())["failed"] == 1
    cur = await db_conn.execute("select reminder_sent from tours_and_appointments where id=%s", (tour["id"],))
    assert (await cur.fetchone())["reminder_sent"] is False

    services.telegram = MockTelegramService()
    assert (await proactive.tour_reminders())["sent"] == 1


async def test_web_buyers_get_the_reminder_in_their_transcript(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    web = await make_buyer(repo, db_conn, "webtour", telegram=False)
    await book(repo, db_conn, web, datetime.now(UTC) + timedelta(hours=5), NOAH)
    assert (await proactive.tour_reminders())["sent"] == 1
    assert services.telegram.messages == [] and "site visit" in (await repo.list_messages(web["id"]))[0]["content"]


# ---------------- C: milestones and commission ----------------

FIXED = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)


async def signed(repo, db_conn, buyer, like, due: date, status="CONTRACT_SIGNED"):
    (row,) = await repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(await pid(db_conn, like), "em")])
    return await repo.update_ledger(row["id"], status=status, next_buyer_milestone_date=due)


async def test_buyer_is_reminded_a_few_days_before_and_again_on_the_day(repo, db_conn):
    proactive, services, _ = make_proactive(repo, now=lambda: FIXED)
    abel = await make_buyer(repo, db_conn, "pay")
    row = await signed(repo, db_conn, abel, NOAH, date(2026, 11, 4))

    assert (await proactive.milestone_reminders())["sent"] == 1
    assert "due in 2 days (04 Nov 2026)" in services.telegram.messages[0]["text"] and "Noah Real Estate" in services.telegram.messages[0]["text"]
    assert (await proactive.milestone_reminders())["sent"] == 0, "same day, same reminder: once"

    on_the_day = make_proactive(repo, now=lambda: FIXED + timedelta(days=2), services=services)[0]
    assert (await on_the_day.milestone_reminders())["sent"] == 1
    assert "is due today" in services.telegram.messages[1]["text"]
    assert len(services.telegram.messages) == 2


async def test_no_reminder_when_the_payment_is_still_far_off(repo, db_conn):
    proactive, services, _ = make_proactive(repo, now=lambda: FIXED)
    await signed(repo, db_conn, await make_buyer(repo, db_conn, "far"), NOAH, date(2026, 11, 20))
    assert (await proactive.milestone_reminders())["sent"] == 0 and services.telegram.messages == []


async def test_an_overdue_payment_alerts_the_broker_not_the_buyer(repo, db_conn):
    proactive, services, _ = make_proactive(repo, now=lambda: FIXED, broker_telegram_chat_id="999")
    await signed(repo, db_conn, await make_buyer(repo, db_conn, "late"), NOAH, date(2026, 10, 30))
    assert (await proactive.milestone_reminders())["sent"] == 1
    assert [m["chat_id"] for m in services.telegram.messages] == ["999"] and "Milestone overdue" in services.telegram.messages[0]["text"]
    assert (await proactive.milestone_reminders())["sent"] == 0


async def test_commission_ready_is_announced_to_the_broker_once(repo, db_conn):
    proactive, services, _ = make_proactive(repo, now=lambda: FIXED, broker_telegram_chat_id="999")
    await signed(repo, db_conn, await make_buyer(repo, db_conn, "paid"), NOAH, date(2026, 12, 1), status="COMMISSION_PENDING")
    assert (await proactive.milestone_reminders())["sent"] == 1
    assert "Commission ready to collect" in services.telegram.messages[0]["text"] and "495,000 ETB" in services.telegram.messages[0]["text"]
    assert (await proactive.milestone_reminders())["sent"] == 0


async def test_broker_notices_still_land_in_the_feed_without_a_telegram_chat(repo, db_conn):
    proactive, services, _ = make_proactive(repo, now=lambda: FIXED, broker_telegram_chat_id="", demo_fallback_telegram_chat_id="")
    await signed(repo, db_conn, await make_buyer(repo, db_conn, "feed"), NOAH, date(2026, 12, 1), status="COMMISSION_PENDING")
    await proactive.milestone_reminders()
    feed = await repo.list_events(audience="broker")
    assert services.telegram.messages == [] and [(e["kind"], e["channel"]) for e in feed] == [("COMMISSION_READY", "feed")]


# ---------------- all together ----------------

async def test_the_daily_run_covers_all_three_jobs_and_is_safe_to_repeat(repo, db_conn):
    proactive, services, _ = make_proactive(repo)
    abel = await make_buyer(repo, db_conn, "all")
    await repo.update_property_price(await pid(db_conn, NOAH), 17_500_000)
    await book(repo, db_conn, abel, datetime.now(UTC) + timedelta(hours=6), GIFT)

    first = await proactive.run_all()
    assert [j["job"] for j in first["jobs"]] == ["reverse_rag_alerts", "tour_reminders", "milestone_reminders"] and first["sent"] == 2
    second = await proactive.run_all()
    assert second["sent"] == 0 and len(services.telegram.messages) == 2


async def test_a_stale_reservation_from_a_crashed_run_is_retried_later(repo, db_conn):
    first = await repo.claim_event(kind="PRICE_DROP", audience="buyer", dedupe_key="crash-test")
    assert first is not None and await repo.claim_event(kind="PRICE_DROP", audience="buyer", dedupe_key="crash-test") is None
    await db_conn.execute("update proactive_events set created_at = now() - interval '11 minutes' where id = %s", (first["id"],))
    assert await repo.claim_event(kind="PRICE_DROP", audience="buyer", dedupe_key="crash-test") is not None
