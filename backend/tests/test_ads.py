"""Channel ads and the deep links behind their buttons, on the real database (rolled back) with a mock Telegram."""
import httpx
import pytest

from app.ads import DEEPLINK, AdError, AdService, deep_link, keyboard, property_code
from app.main import create_app
from app.services.telegram import MockTelegramService, TelegramError
from app.telegram_updates import parse_update
from tests.agent_helpers import FakeGateway, ext, make_chat, make_services
from tests.test_ingest_service import make as make_ingest

CHANNEL = "-1001234567890"
ADMIN = {"X-Admin-Key": "test-admin-key"}


async def prop(db_conn, like="Noah Bole Towers 3BR%"):
    cur = await db_conn.execute("select id from properties where title like %s", (like,))
    return (await cur.fetchone())["id"]


def make_ads(repo, services=None, gateway=None, **settings):
    from app.config import get_settings

    services = services or make_services()
    return AdService(repo, gateway or FakeGateway(), services, get_settings().model_copy(update={"telegram_channel_id": CHANNEL, **settings})), services


# ---------------- pure pieces ----------------

def test_deep_links_carry_a_short_code_and_match_the_pattern_the_chat_accepts():
    pid = "0b303482-dee9-414e-ae2d-49da581e2683"
    assert property_code(pid) == "0b303482dee9"[:8] == "0b303482"
    assert deep_link("prop_plus_bot", "ask", pid) == "https://t.me/prop_plus_bot?start=ask_0b303482"
    for action in ("ask", "cmp", "book"):
        assert DEEPLINK.match(f"{action}_0b303482")
    for bad in ("ask_0b30348", "ask_0B303482", "buy_0b303482", "ask_0b303482x", "ask_", "", "ask_zzzzzzzz"):
        assert DEEPLINK.match(bad) is None


def test_the_keyboard_has_the_three_agreed_buttons_all_linking_into_the_bot():
    markup = keyboard("prop_plus_bot", "0b303482-dee9-414e-ae2d-49da581e2683")
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == ["Ask AI About This Unit", "Compare With Similar", "Book Site Visit"]
    assert [b["url"].split("start=")[1].split("_")[0] for b in buttons] == ["ask", "cmp", "book"]
    assert all(b["url"].startswith("https://t.me/prop_plus_bot?start=") and len(b["url"].split("start=")[1]) <= 64 for b in buttons)


def test_start_payloads_reach_the_chat_and_a_bare_start_is_still_a_greeting():
    def update(text):
        return {"update_id": 1, "message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 5, "is_bot": False}, "text": text}}

    linked = parse_update(update("/start ask_0b303482"))
    assert (linked.text, linked.start_payload) == ("Hello", "ask_0b303482")
    assert parse_update(update("/start@PropPlusBot book_0b303482")).start_payload == "book_0b303482"
    bare = parse_update(update("/start"))
    assert (bare.text, bare.start_payload) == ("Hello", None)
    assert parse_update(update("hello there")).start_payload is None


# ---------------- composing ----------------

async def test_an_ad_states_database_facts_and_the_model_only_writes_the_wording(repo, db_conn):
    ads, _ = make_ads(repo)
    p = (await repo.get_properties([await prop(db_conn)]))[0]
    text = await ads.compose(p, kind="NEW_LISTING")
    assert text.startswith("HEADLINE\n\nPITCH\n\nNoah Bole Towers 3BR Apartment\nNoah Real Estate - Bole\nPrice: 19.8M ETB (146,667 ETB/sqm)")
    assert "Down payment: 30% (5.94M ETB) | Delivery: Mar 2027" in text and "Features: " in text


async def test_a_price_drop_ad_shows_the_old_and_new_price(repo, db_conn):
    ads, _ = make_ads(repo)
    p = (await repo.get_properties([await prop(db_conn)]))[0]
    text = await ads.compose(p, kind="PRICE_DROP", old_price=23_000_000)
    assert "Was 23M ETB, now 19.8M ETB (-13.9%)" in text


async def test_if_the_model_is_down_the_ad_still_reads_well(repo, db_conn):
    gw = FakeGateway()
    gw.fail_ad_copy = True
    ads, _ = make_ads(repo, gateway=gw)
    p = (await repo.get_properties([await prop(db_conn)]))[0]
    text = await ads.compose(p, kind="NEW_LISTING")
    assert text.startswith("New listing: Noah Bole Towers 3BR Apartment") and "Price: 19.8M ETB" in text


# ---------------- publishing ----------------

async def test_a_preview_builds_the_ad_but_posts_nothing_and_records_nothing(repo, db_conn):
    ads, services = make_ads(repo)
    result = await ads.publish(await prop(db_conn), dry_run=True)
    assert result["posted"] is False and "HEADLINE" in result["preview"] and len(result["buttons"]) == 3
    assert services.telegram.messages == [] and services.telegram.photos == [] and await repo.list_channel_posts() == []


async def test_publishing_posts_to_the_channel_with_buttons_and_records_it(repo, db_conn):
    ads, services = make_ads(repo)
    pid = await prop(db_conn)
    result = await ads.publish(pid, kind="NEW_LISTING", dry_run=False)
    assert result["posted"] and result["message_id"] == 1
    (message,) = services.telegram.messages
    assert message["chat_id"] == CHANNEL and "HEADLINE" in message["text"]
    urls = [b["url"] for row in message["reply_markup"]["inline_keyboard"] for b in row]
    assert urls[0] == f"https://t.me/mock_bot?start=ask_{property_code(pid)}"
    (post,) = await repo.list_channel_posts()
    assert post["kind"] == "NEW_LISTING" and post["telegram_message_id"] == 1 and float(post["price_etb"]) == 19_800_000


async def test_the_same_announcement_is_never_posted_twice_but_a_new_price_is_news(repo, db_conn):
    ads, services = make_ads(repo)
    pid = await prop(db_conn)
    await ads.publish(pid, kind="PRICE_DROP", dry_run=False, old_price=23_000_000)
    again = await ads.publish(pid, kind="PRICE_DROP", dry_run=False, old_price=23_000_000)
    assert again == {"posted": False, "reason": "This exact announcement was already posted."} and len(services.telegram.messages) == 1
    await repo.update_property_price(pid, 18_000_000)
    assert (await ads.publish(pid, kind="PRICE_DROP", dry_run=False, old_price=19_800_000))["posted"] and len(services.telegram.messages) == 2


async def test_a_manual_post_can_be_repeated_on_purpose(repo, db_conn):
    ads, services = make_ads(repo)
    pid = await prop(db_conn)
    assert (await ads.publish(pid, dry_run=False))["posted"] and (await ads.publish(pid, dry_run=False))["posted"]
    assert len(services.telegram.messages) == 2


async def test_a_unit_with_a_floor_plan_is_posted_as_a_photo_and_falls_back_to_text(repo, db_conn):
    pid = await prop(db_conn)
    await db_conn.execute("update properties set floor_plan_image_url = 'https://cdn.example/plan.png' where id = %s", (pid,))
    ads, services = make_ads(repo)
    assert (await ads.publish(pid, dry_run=False))["posted"]
    assert services.telegram.photos[0]["photo_url"] == "https://cdn.example/plan.png" and services.telegram.messages == []

    class NoPhotos(MockTelegramService):
        async def send_photo(self, *a, **k):
            raise TelegramError("wrong file identifier/HTTP URL specified")

    failing = make_services()
    failing.telegram = NoPhotos()
    ads2, _ = make_ads(repo, services=failing)
    assert (await ads2.publish(pid, dry_run=False))["posted"] and len(failing.telegram.messages) == 1


async def test_a_refused_post_is_reported_and_can_be_retried(repo, db_conn):
    class Refuses(MockTelegramService):
        async def send_message(self, *a, **k):
            raise TelegramError("Forbidden: bot is not a member of the channel chat")

    services = make_services()
    services.telegram = Refuses()
    ads, _ = make_ads(repo, services=services)
    pid = await prop(db_conn)
    with pytest.raises(AdError, match="admin of the channel") as exc:
        await ads.publish(pid, kind="NEW_LISTING", dry_run=False)
    assert "Forbidden" not in str(exc.value) and await repo.list_channel_posts() == []

    working, ok = make_ads(repo)
    assert (await working.publish(pid, kind="NEW_LISTING", dry_run=False))["posted"], "the failed attempt did not use up the announcement"


async def test_nothing_is_posted_without_a_channel_or_for_unavailable_units(repo, db_conn):
    ads, _ = make_ads(repo, telegram_channel_id="")
    with pytest.raises(AdError, match="TELEGRAM_CHANNEL_ID"):
        await ads.publish(await prop(db_conn), dry_run=True)
    ads, _ = make_ads(repo)
    pid = await prop(db_conn)
    await db_conn.execute("update properties set status = 'UNAVAILABLE' where id = %s", (pid,))
    with pytest.raises(AdError, match="not available"):
        await ads.publish(pid, dry_run=True)
    with pytest.raises(LookupError):
        await ads.publish("00000000-0000-0000-0000-000000000000", dry_run=True)


async def test_ads_for_an_ingest_run_cover_new_units_and_price_cuts_only(repo, db_conn):
    from app.ingest.models import ScrapeProfile
    from app.ingest.sample import sample_listing

    ingest, _, _ = make_ingest(repo)
    html, profile = sample_listing(2)
    report = await ingest.run(developer_slug="ayat", source="html", html=html, profile=ScrapeProfile(**profile), full_sync=True)
    ads, services = make_ads(repo)
    results = await ads.announce_report(report)
    assert sorted((r["kind"], r["title"], r["posted"]) for r in results) == [
        ("NEW_LISTING", "Ayat Garden Residences 2BR Townhouse", True), ("PRICE_DROP", "Ayat Heights 2BR Apartment", True)]
    assert len(services.telegram.messages) == 2, "a removed unit is never advertised"
    assert any("Was 6.8M ETB, now 6.2M ETB (-8.8%)" in m["text"] for m in services.telegram.messages)
    again = await ads.announce_report(report)
    assert all(r["posted"] is False for r in again) and len(services.telegram.messages) == 2


# ---------------- channel button taps ----------------

async def tap(chat, services, repo, action, pid, user="990001"):
    from app.chat import Inbound

    return await chat.handle_inbound(Inbound(channel="telegram", external_id=user, chat_id=int(user), text="Hello", start_payload=f"{action}_{property_code(pid)}"))


async def test_ask_replies_with_the_units_details_and_makes_it_option_one(repo, db_conn):
    chat, services, agent = make_chat(repo, FakeGateway())
    pid = await prop(db_conn)
    out = await tap(chat, services, repo, "ask", pid)
    assert "1. Noah Bole Towers 3BR Apartment - Noah Real Estate, Bole" in out.reply and "compare it with similar options" in out.reply
    assert services.telegram.messages[0]["chat_id"] == 990001
    state = (await agent.graph.aget_state({"configurable": {"thread_id": "tg:990001"}})).values
    assert [m["title"] for m in state["active_matches"]] == ["Noah Bole Towers 3BR Apartment"]
    buyer = await repo.get_buyer_by_thread("tg:990001")
    rows = await repo.list_messages(buyer["id"])
    assert [r["content"] for r in rows][0] == "Opened a channel ad: Ask AI About This Unit"


async def test_book_starts_a_booking_for_that_unit_and_asks_for_contact_details(repo, db_conn):
    gw = FakeGateway([ext("book_tour", referenced_options=[1])])
    chat, services, agent = make_chat(repo, gw)
    out = await tap(chat, services, repo, "book", await prop(db_conn))
    assert "full name and phone number" in out.reply, "the buyer is asked for what a lead registration needs"
    assert gw.prompts("structured") == [], "the button's sentence is read in code, so no model call is spent on it"


async def test_compare_loads_the_unit_with_similar_ones_and_runs_the_comparison(repo, db_conn):
    gw = FakeGateway([ext("compare")])
    chat, services, agent = make_chat(repo, gw)
    out = await tap(chat, services, repo, "cmp", await prop(db_conn))
    state = (await agent.graph.aget_state({"configurable": {"thread_id": "tg:990001"}})).values
    titles = list(state["comparison_matrix"])
    assert titles[0] == "Noah Bole Towers 3BR Apartment" and len(titles) >= 2, "the tapped unit is compared with at least one similar unit"
    assert out.reply == "LLM-REPLY"


async def test_a_stale_or_forged_button_is_handled_politely(repo, db_conn):
    chat, services, _ = make_chat(repo, FakeGateway([ext("small_talk")]))
    pid = await prop(db_conn)
    await db_conn.execute("update properties set status = 'UNAVAILABLE' where id = %s", (pid,))
    gone = await tap(chat, services, repo, "ask", pid)
    assert "no longer available" in gone.reply
    from app.chat import Inbound

    forged = await chat.handle_inbound(Inbound(channel="telegram", external_id="990002", chat_id=990002, text="Hello", start_payload="ask_ffffffff"))
    assert "no longer available" in forged.reply
    unknown = await chat.handle_inbound(Inbound(channel="telegram", external_id="990003", chat_id=990003, text="Hello", start_payload="whatever"))
    assert unknown.reply == "LLM-REPLY", "an unrecognised payload is just a normal greeting"


async def test_a_buyer_whose_chat_the_broker_holds_is_not_answered_by_the_bot(repo, db_conn):
    chat, services, agent = make_chat(repo, FakeGateway())
    buyer = await repo.get_or_create_buyer("tg:990004", 990004)
    await chat.set_takeover(str(buyer["id"]), True)
    out = await tap(chat, services, repo, "ask", await prop(db_conn), user="990004")
    assert out.takeover and out.reply is None and services.telegram.messages == []


# ---------------- API ----------------

async def api_client(repo, gateway=None):
    chat, services, _ = make_chat(repo, FakeGateway(), admin_api_key="test-admin-key")
    ads, _ = make_ads(repo, services=services, gateway=gateway)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.ads = chat.settings, repo, chat, ads
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), services


async def test_the_ads_api_previews_by_default_and_only_posts_when_told_to(spy_repo, db_conn):
    client, services = await api_client(spy_repo)
    pid = str(await prop(db_conn))
    async with client:
        assert (await client.post("/api/ads/publish", json={"property_id": pid})).status_code == 401
        preview = await client.post("/api/ads/publish", json={"property_id": pid}, headers=ADMIN)
        assert preview.status_code == 200 and preview.json()["posted"] is False and services.telegram.messages == []
        posted = await client.post("/api/ads/publish", json={"property_id": pid, "dry_run": False}, headers=ADMIN)
        assert posted.json()["posted"] is True and len(services.telegram.messages) == 1
        listing = (await client.get("/api/ads", headers=ADMIN)).json()
        assert listing[0]["title"] == "Noah Bole Towers 3BR Apartment" and listing[0]["kind"] == "MANUAL"


async def test_the_ads_api_rejects_bad_input(spy_repo):
    client, _ = await api_client(spy_repo)
    async with client:
        assert (await client.post("/api/ads/publish", json={"property_id": "not-a-uuid"}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/ads/publish", json={"property_id": "00000000-0000-0000-0000-000000000000", "kind": "SPAM"}, headers=ADMIN)).status_code == 422
        assert (await client.post("/api/ads/publish", json={"property_id": "00000000-0000-0000-0000-000000000000"}, headers=ADMIN)).status_code == 404
