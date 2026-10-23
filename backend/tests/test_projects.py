"""Developers that publish no unit prices: crawling their site, showing projects, price requests, and offers advertised in the channel."""
from datetime import date, timedelta

import pytest

from app.ingest.fetch import FetchError
from app.ingest.projects import Page, ProjectInfo, crawl, looks_like_a_project, read_projects, to_row
from app.projects import ProjectService, card_text, has_live_offer
from app.services.telegram import MockTelegramService, TelegramError
from tests.agent_helpers import NOW, FakeGateway, ext, make_agent, make_chat, make_proactive, make_services, new_buyer, say_to

pytestmark = pytest.mark.usefixtures("quiet_world")

BODY = ("Discover the project: spacious apartments for sale in a prime residential area, 21 floors with 3 to 4 bedrooms, sizes from 126 to 185 sqm, "
        "parking, a gym and round-the-clock security. Selling now. " * 3)


def project_page(title="Central Tower", extra=""):
    return f"<html><head><meta property='og:image' content='/img/{title.split()[0].lower()}.jpg'></head><body><nav><a href='/about'>About</a></nav><h1>{title}</h1><p>{BODY}{extra}</p></body></html>"


class Site:
    """A fetcher over a dictionary of pages."""

    def __init__(self, pages):
        self.pages, self.fetched = pages, []

    async def get_text(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise FetchError("HTTP 404")
        return self.pages[url]

    async def get_bytes(self, url, **kw):
        return b"IMG", "image/jpeg"


class Ads:
    async def bot_username(self):
        return "proppulse_bot"


def info(name="Central Tower", **kw):
    base = dict(is_project_page=True, name=name, sub_city="Bole", location_text="In the diplomatic hub", summary="City views.", bedrooms_min=3, bedrooms_max=4,
                sqm_min=126, sqm_max=185, floors=21, status="SELLING")
    return ProjectInfo(**{**base, **kw})


async def developer_id(db_conn, slug="noah"):
    cur = await db_conn.execute("select id from developers where slug = %s", (slug,))
    return (await cur.fetchone())["id"]


def make_projects(repo, gateway=None, fetcher=None, **settings):
    proactive, services, _ = make_proactive(repo, **{"broker_telegram_chat_id": "999", "telegram_channel_id": "@channel", **settings})
    service = ProjectService(repo, gateway or FakeGateway(), services, proactive.settings, fetcher=fetcher or Site({}), proactive=proactive, ads=Ads(), now=lambda: NOW)
    return service, services


async def add_project(repo, db_conn, name="Central Tower", slug_dev="noah", **kw):
    row = to_row(info(name, **kw), Page(f"https://dev.example/{name.lower().replace(' ', '-')}/", BODY, "https://dev.example/img.jpg"))
    saved, _ = await repo.upsert_project(await developer_id(db_conn, slug_dev), row)
    return saved


# ---------------- crawling ----------------

async def test_the_crawl_stays_on_one_site_skips_noise_and_respects_the_page_limit():
    site = Site({
        "https://dev.example/": "<a href='/residential/'>Homes</a><a href='https://other.example/x'>Elsewhere</a><a href='/blog/post'>Blog</a><a href='/logo.png'>logo</a>",
        "https://dev.example/residential/": "<a href='/residential/tower/'>Tower</a><a href='/residential/villa/'>Villa</a>",
        "https://dev.example/residential/tower/": project_page(),
        "https://dev.example/residential/villa/": project_page("Garden Villa"),
    })
    pages, notes = await crawl(site, "https://dev.example/", max_pages=10)
    assert [p.url for p in pages] == ["https://dev.example/", "https://dev.example/residential/", "https://dev.example/residential/tower/", "https://dev.example/residential/villa/"]
    assert not any("other.example" in u or "/blog" in u or ".png" in u for u in site.fetched), "other sites, blog pages and images are never fetched"
    assert pages[2].image == "https://dev.example/img/central.jpg" and notes == []
    limited, _ = await crawl(site, "https://dev.example/", max_pages=2)
    assert len(limited) == 2


async def test_a_page_that_cannot_be_fetched_is_reported_not_fatal():
    site = Site({"https://dev.example/": "<a href='/gone/'>x</a>"})
    pages, notes = await crawl(site, "https://dev.example/")
    assert len(pages) == 1 and notes == ["https://dev.example/gone/: HTTP 404"]


def test_generic_words_do_not_make_a_different_project():
    from app.ingest.projects import slugify

    assert slugify("Sarbet Gabriel Luxury Apartments") == slugify("Sarbet Gabriel Apartments") == slugify("Sarbet Gabriel") == "sarbet-gabriel"
    assert slugify("Metropolitan Residence") == "metropolitan-residence" and slugify("Apartments") == "apartments"


def test_only_pages_that_talk_about_homes_go_to_the_model():
    assert looks_like_a_project(Page("u", BODY, None))
    assert not looks_like_a_project(Page("u", "We were founded in 1997. Contact us. " * 30, None))


async def test_unchanged_pages_cost_no_model_call_and_bad_answers_are_dropped():
    page = Page("https://dev.example/t/", BODY, None)
    gw = FakeGateway([info()])
    rows, _ = await read_projects(gw, [page])
    assert len(rows) == 1 and rows[0]["name"] == "Central Tower" and len(gw.calls) == 1
    again, _ = await read_projects(gw, [page], known={page.url: rows[0]["content_hash"]})
    assert again == [{"source_url": page.url, "content_hash": rows[0]["content_hash"], "unchanged": True}] and len(gw.calls) == 1
    not_a_project, _ = await read_projects(FakeGateway([ProjectInfo(is_project_page=False)]), [page])
    assert not_a_project == []


def test_what_the_model_says_is_checked_before_it_is_kept():
    page = Page("https://dev.example/t/", BODY, None)
    row = to_row(info(bedrooms_min=4, bedrooms_max=2, sqm_min=-5, sqm_max=185, floors=9999, status="weird", promotion="10% off until August",
                      promotion_discount_pct=250, promotion_until="2099-08-31"), page)
    assert (row["bedrooms_min"], row["bedrooms_max"]) == (2, 4), "a reversed range is put right"
    assert row["sqm_min"] is None and row["sqm_max"] == 185 and row["floors"] is None and row["status"] == "SELLING"
    assert row["promo_text"] == "10% off until August" and row["promo_discount_pct"] is None and row["promo_until"] == date(2099, 8, 31)
    assert to_row(info(promotion="", promotion_discount_pct=10), page)["promo_discount_pct"] is None, "no offer, no discount"
    assert to_row(ProjectInfo(is_project_page=True, name="X"), page) is None, "a name that short is not a project"


async def test_crawling_a_developer_previews_then_saves_and_then_finds_nothing_new(repo, db_conn):
    site = Site({"https://dev.example/": "<a href='/t/'>t</a>", "https://dev.example/t/": project_page()})
    service, _ = make_projects(repo, FakeGateway([info(), info()]), site)
    preview = await service.crawl_developer("noah", "https://dev.example/", dry_run=True)
    assert [p["name"] for p in preview["created"]] == ["Central Tower"] and await repo.list_projects() == []
    saved = await service.crawl_developer("noah", "https://dev.example/", dry_run=False)
    assert [p["name"] for p in saved["created"]] == ["Central Tower"] and len(await repo.list_projects()) == 1
    again = await service.crawl_developer("noah", "https://dev.example/", dry_run=False)
    assert again["created"] == [] and again["unchanged"] == ["https://dev.example/t/"], "an unchanged page is not read by the model again"


async def test_the_same_project_found_on_two_pages_is_kept_once(repo):
    site = Site({"https://dev.example/": "<a href='/a/'>a</a><a href='/b/'>b</a>", "https://dev.example/a/": project_page(), "https://dev.example/b/": project_page(extra=" Also see our gallery.")})
    service, _ = make_projects(repo, FakeGateway([info(), info()]), site)
    report = await service.crawl_developer("noah", "https://dev.example/", dry_run=False)
    assert [p["source_url"] for p in report["created"]] == ["https://dev.example/a/"] and len(await repo.list_projects()) == 1
    assert any("Skipped a duplicate of Central Tower" in n for n in report["notes"])


async def test_a_site_with_no_project_pages_says_so(repo):
    service, _ = make_projects(repo, FakeGateway(), Site({"https://dev.example/": "<p>Hello</p>"}))
    report = await service.crawl_developer("noah", "https://dev.example/")
    assert report["created"] == [] and any("No page looked like" in n for n in report["notes"])


# ---------------- repository ----------------

async def test_projects_are_created_updated_and_left_alone_when_unchanged(repo, db_conn):
    dev = await developer_id(db_conn)
    page = Page("https://dev.example/t/", BODY, None)
    row = to_row(info(), page)
    _, first = await repo.upsert_project(dev, row)
    _, same = await repo.upsert_project(dev, row)
    _, changed = await repo.upsert_project(dev, {**row, "content_hash": "new", "status": "SOLD_OUT"})
    assert (first, same, changed) == ("created", "unchanged", "updated")
    assert (await repo.list_projects())[0]["status"] == "SOLD_OUT"


async def test_search_filters_by_area_and_size_and_puts_live_offers_first(repo, db_conn):
    await add_project(repo, db_conn, "Plain Heights", sub_city="Bole")
    await add_project(repo, db_conn, "Offer Towers", sub_city="Bole", promotion="5% off", promotion_until=(date.today() + timedelta(days=5)).isoformat())
    await add_project(repo, db_conn, "Old Offer Court", sub_city="Bole", promotion="gone", promotion_until=(date.today() - timedelta(days=1)).isoformat())
    await add_project(repo, db_conn, "Sold Place", sub_city="Bole", status="SOLD_OUT")
    await add_project(repo, db_conn, "Far Away", sub_city="Ayat")
    await add_project(repo, db_conn, "Small Studios", sub_city="Bole", bedrooms_min=1, bedrooms_max=1)
    found = await repo.search_projects(sub_cities=["Bole"], min_bedrooms=3, limit=5)
    assert [p["name"] for p in found] == ["Offer Towers", "Old Offer Court", "Plain Heights"], "live offer first, sold-out, other areas and too-small projects left out"
    assert {p["name"] for p in await repo.search_projects(sub_cities=[], limit=10)} >= {"Far Away", "Plain Heights"}


# ---------------- buyers ask for a price ----------------

async def test_asking_the_price_tells_the_broker_once_and_a_second_ask_only_waits(repo, db_conn):
    service, services = make_projects(repo)
    await add_project(repo, db_conn)
    abel = await new_buyer(repo, "askprice")
    text, buttons = await service.handle_text(abel, "Ask the price of Central Tower")
    assert "asked Noah Real Estate for the price of Central Tower" in text and buttons == []
    assert len(services.telegram.messages) == 1 and "wants the price of Central Tower" in services.telegram.messages[0]["text"]
    assert services.telegram.messages[0]["chat_id"] == "999"
    again, _ = await service.handle_text(abel, "ask the price of central tower")
    assert "already asked" in again and len(services.telegram.messages) == 1, "the broker is not told twice"
    assert [r["status"] for r in await repo.list_price_requests()] == ["OPEN"]


async def test_naming_a_project_shows_its_card_with_an_ask_button_and_a_priced_one_says_the_price(repo, db_conn):
    service, _ = make_projects(repo)
    project = await add_project(repo, db_conn, promotion="10% off for reservations", promotion_until=(date.today() + timedelta(days=9)).isoformat(), promotion_discount_pct=10)
    abel = await new_buyer(repo, "card")
    text, buttons = await service.handle_text(abel, "tell me about central tower please")
    assert "Central Tower - Noah Real Estate" in text and "126-185 sqm" in text and "3-4 bedrooms" in text and "21 floors" in text
    assert "Offer: 10% off for reservations (until" in text and "Price: on request" in text
    assert buttons == [[{"label": "Ask the price", "text": "Ask the price of Central Tower"}, {"label": "Book a visit", "text": "Book a visit to Central Tower"}]]
    assert await service.handle_text(abel, "I want a 3 bedroom in Bole") is None, "other messages are left to the agent"
    await repo.set_project_price(project["id"], 19_500_000, "from the 3 bedroom")
    service._names = (0.0, [])
    known, _ = await service.handle_text(abel, "Ask the price of Central Tower")
    assert "starts from 19.5M ETB (from the 3 bedroom)" in known


async def test_a_channel_ad_button_opens_the_project_or_asks_the_price(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn)
    code = str(project["id"]).replace("-", "")[:8]
    abel = await new_buyer(repo, "deeplink")
    info_text, buttons = await service.handle_link(abel, f"pinfo_{code}")
    assert "Central Tower" in info_text and buttons
    asked, _ = await service.handle_link(abel, f"proj_{code}")
    assert "asked Noah Real Estate" in asked and len(services.telegram.messages) == 1
    unknown, _ = await service.handle_link(abel, "proj_00000000")
    assert "could not find that project" in unknown and await service.handle_link(abel, "ask_12345678") is None


async def test_the_developer_is_emailed_only_when_that_is_switched_on(repo, db_conn):
    off, services_off = make_projects(repo)
    await add_project(repo, db_conn)
    await off.handle_text(await new_buyer(repo, "mail1"), "Ask the price of Central Tower")
    assert services_off.email.sent == []
    on, services_on = make_projects(repo, project_price_request_email=True)
    await on.handle_text(await new_buyer(repo, "mail2"), "Ask the price of Central Tower")
    assert [m["to"] for m in services_on.email.sent] == ["sales@noah.example"] and "Price list request" in services_on.email.sent[0]["subject"]
    assert "Abel" not in services_on.email.sent[0]["html"], "no buyer details go to the developer"


async def test_setting_the_price_tells_every_waiting_buyer_once_and_the_broker_for_held_chats(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn)
    waiting = [await repo.get_or_create_buyer(f"test:wait{i}", 6_000_100 + i) for i in range(2)]
    held = await repo.get_or_create_buyer("test:held", 6_000_200)
    await db_conn.execute("update buyers_crm set human_takeover = true where id = %s", (held["id"],))
    held = await repo.get_buyer(held["id"])
    for b in [*waiting, held]:
        await service.handle_text(b, "Ask the price of Central Tower")
    services.telegram.messages.clear()

    result = await service.set_price(project["id"], 21_000_000, "starting price")
    assert (result["buyers_told"], result["skipped"]) == (2, 1)
    to_buyers = [m for m in services.telegram.messages if m["chat_id"] != "999"]
    assert sorted(m["chat_id"] for m in to_buyers) == [6_000_100, 6_000_101] and all("starts from 21M ETB (starting price)" in m["text"] for m in to_buyers)
    assert any(m["chat_id"] == "999" and "asked for it in a chat you hold" in m["text"] for m in services.telegram.messages)
    again = await service.set_price(project["id"], 21_000_000, "starting price")
    assert again["buyers_told"] == 0, "nobody is told twice"
    assert [r["status"] for r in await repo.list_price_requests()].count("ANSWERED") == 2
    with pytest.raises(Exception):
        await service.set_price(project["id"], -5)


# ---------------- offers in the channel ----------------

async def test_an_offer_is_advertised_once_with_the_picture_and_again_only_when_it_changes(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn, promotion="10% off until August", promotion_until=(date.today() + timedelta(days=20)).isoformat())
    await add_project(repo, db_conn, "No Offer Place")

    preview = await service.run_promotion_ads(dry_run=True)
    assert preview["details"] == [{"project": "Central Tower", "would_post": "10% off until August"}] and services.telegram.photos == []
    sent = await service.run_promotion_ads()
    assert sent["sent"] == 1 and len(services.telegram.photos) == 1
    post = services.telegram.photos[0]
    assert post["photo_url"] == "https://dev.example/img.jpg" and "Offer: 10% off until August" in post["caption"] and "Price: on request" in post["caption"]
    buttons = [b["text"] for row in post["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == ["Ask the price", "Book a visit", "More about this project"]
    assert post["reply_markup"]["inline_keyboard"][0][0]["url"].startswith("https://t.me/proppulse_bot?start=proj_")
    assert (await service.run_promotion_ads())["sent"] == 0, "the same offer is not posted twice"

    await repo.set_project_promo(project["id"], "15% off this week", 15, date.today() + timedelta(days=7))
    assert (await service.run_promotion_ads())["sent"] == 1, "a new offer is announced"
    await repo.set_project_promo(project["id"], "expired", None, date.today() - timedelta(days=1))
    assert (await service.run_promotion_ads())["sent"] == 0 and not has_live_offer(await repo.get_project(project["id"]))


async def test_a_sold_out_project_is_never_advertised_and_a_blocked_picture_is_uploaded(repo, db_conn):
    service, services = make_projects(repo)
    sold = await add_project(repo, db_conn, "Gone Heights", status="SOLD_OUT")
    with pytest.raises(Exception, match="sold out"):
        await service.publish_ad(sold["id"], dry_run=False)

    class CannotFetch(MockTelegramService):
        async def send_photo(self, *a, **k):
            raise TelegramError("failed to get HTTP URL content")

    blocked = make_services()
    blocked.telegram = CannotFetch()
    proactive, _, _ = make_proactive(repo, services=blocked, telegram_channel_id="@channel")
    service2 = ProjectService(repo, FakeGateway(), blocked, proactive.settings, fetcher=Site({}), proactive=proactive, ads=Ads())
    live = await add_project(repo, db_conn, "Open Heights")
    assert (await service2.publish_ad(live["id"], dry_run=False))["posted"]
    assert blocked.telegram.photos[0]["photo_bytes"] == 3 and blocked.telegram.messages == [], "the picture was downloaded and uploaded"


# ---------------- the agent offers projects when nothing priced fits ----------------

async def test_a_buyer_whose_search_finds_nothing_is_offered_projects_in_the_area(repo, db_conn):
    await add_project(repo, db_conn, "Bole Heights", sub_city="Bole")
    gw = FakeGateway([ext(max_budget_etb=1_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"])])
    agent, buyer = make_agent(repo, gw), await new_buyer(repo, "offerprojects")
    res = await say_to(agent, buyer, "3 bedroom in Bole for 1 million")
    assert "These projects are in your area" in res.reply and "Bole Heights - Noah Real Estate" in res.reply and "Price: on request" in res.reply
    assert [b["text"] for row in res.state["quick_replies"] for b in row] == ["Ask the price of Bole Heights", "Book a visit to Bole Heights"]


async def test_the_whole_chat_path_handles_the_ask_button_without_the_model(repo, db_conn):
    gw = FakeGateway()
    chat, services, _ = make_chat(repo, gw, broker_telegram_chat_id="999")
    proactive, _, _ = make_proactive(repo, services=services, broker_telegram_chat_id="999")
    chat.projects = ProjectService(repo, gw, services, chat.settings, fetcher=Site({}), proactive=proactive, ads=Ads())
    await add_project(repo, db_conn)
    from app.chat import Inbound

    out = await chat.handle_inbound(Inbound(channel="web", external_id="proj-chat-0001", kind="text", text="Ask the price of Central Tower"))
    assert "asked Noah Real Estate" in out.reply and gw.calls == [], "no model was asked"
    assert any(m["chat_id"] == "999" for m in services.telegram.messages)
    started = await chat.handle_inbound(Inbound(channel="web", external_id="proj-chat-0002", kind="text", text="Hello", start_payload="pinfo_" + str((await repo.list_projects())[0]["id"]).replace("-", "")[:8]))
    assert "Central Tower" in started.reply and started.quick_replies


def test_card_text_without_a_price_or_offer_is_plain():
    text = card_text({"name": "X Tower", "developer_name": "Dev", "status": "UPCOMING", "sub_city": None, "location_text": None, "summary": None})
    assert text == "X Tower - Dev\nComing soon\nPrice: on request"


async def test_seeding_the_demo_projects_alone_keeps_crawled_ones(repo, db_conn):
    from app.demo_projects import restore_demo_projects

    crawled = await add_project(repo, db_conn, "Real Crawled Place")
    out = await restore_demo_projects(repo, remove_crawled=False)
    assert out["removed_crawled_projects"] == 0 and crawled["id"] in {p["id"] for p in await repo.list_projects()}


async def test_reset_removes_crawled_projects_and_restores_the_demo_ones_without_prices(repo, db_conn):
    from app.demo_projects import restore_demo_projects

    crawled = await add_project(repo, db_conn, "Crawled Place")
    first = await restore_demo_projects(repo)
    assert first["removed_crawled_projects"] >= 1 and first["restored_demo_projects"] == 3
    names = {p["name"] for p in await repo.list_projects()}
    assert "Crawled Place" not in names and {"Ayat Sky Residences", "Noah Park Terraces", "Gift Garden Court"} <= names
    terraces = next(p for p in await repo.list_projects() if p["name"] == "Noah Park Terraces")
    await repo.set_project_price(terraces["id"], 12_000_000, None)
    await restore_demo_projects(repo)
    again = next(p for p in await repo.list_projects() if p["name"] == "Noah Park Terraces")
    assert again["price_from_etb"] is None and has_live_offer(again), "back to price on request, with a live demo offer"
    assert again["media_count"] == 1, "a demo project always has a picture to post"
    await restore_demo_projects(repo)
    assert next(p for p in await repo.list_projects() if p["name"] == "Noah Park Terraces")["media_count"] == 1, "restoring twice does not pile up pictures"
    assert crawled["id"] not in {p["id"] for p in await repo.list_projects()}


# ---------------- booking a visit to a project ----------------

async def contactable(repo, tag="visit", telegram=None):
    buyer = await repo.get_or_create_buyer(f"test:{tag}", telegram)
    await repo.update_buyer(buyer["id"], full_name="Abel Tesfaye", phone="0911000000")
    return await repo.get_buyer(buyer["id"])


async def test_a_visit_needs_contact_details_first_then_offers_free_times(repo, db_conn):
    service, _ = make_projects(repo)
    await add_project(repo, db_conn)
    stranger = await new_buyer(repo, "nocontact")
    text, buttons = await service.handle_text(stranger, "Book a visit to Central Tower")
    assert "I need your full name and phone number" in text and buttons == [[{"label": "Book a visit", "text": "Book a visit to Central Tower"}]]
    abel = await contactable(repo)
    text, buttons = await service.handle_text(abel, "book a visit to central tower")
    assert "I can offer these times for Central Tower" in text and len(buttons) == 3
    assert buttons[0][0]["text"] == "Book visit to Central Tower on 2026-11-02 10:00", "the first free time is two hours after the clock"


async def test_booking_a_project_visit_creates_the_event_tour_broker_notice_and_lead_registration(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn)
    abel = await contactable(repo, "booked", telegram=7_100_001)
    reply, buttons = await service.handle_text(abel, "Book visit to Central Tower on 2026-11-03 10:00")
    assert "Your visit to Central Tower is booked for Tue 03 Nov, 10:00" in reply and "informed Noah Real Estate" in reply and buttons == []
    (event,) = services.calendar.events.values()
    assert "Central Tower" in event["description"] and event["summary"] == "Site visit: Abel Tesfaye"
    (tour,) = [t for t in await repo.list_tours() if t["project_name"] == "Central Tower"]
    assert tour["stops"] == [] and tour["kind"] == "SITE_VISIT" and str(tour["project_id"]) == str(project["id"])
    assert (await repo.get_buyer(abel["id"]))["deal_stage"] == "SITE_VISIT_BOOKED"
    assert any(m["chat_id"] == "999" and "booked a visit to Central Tower" in m["text"] for m in services.telegram.messages)
    (mail,) = [m for m in services.email.sent if m["to"] == "sales@noah.example"]  # the broker's dossier email is separate
    assert mail["to"] == "sales@noah.example" and "Lead registration" in mail["subject"] and "Abel Tesfaye" in mail["html"]

    again, other_times = await service.handle_text(await contactable(repo, "second"), "Book visit to Central Tower on 2026-11-03 10:00")
    assert "That time isn't free any more" in again and other_times, "a taken time offers other times"
    past, _ = await service.handle_text(abel, "Book visit to Central Tower on 2026-11-02 08:30")
    assert "I can offer these times" in past


async def test_a_sold_out_project_cannot_be_booked_and_the_reminder_names_the_project(repo, db_conn):
    service, services = make_projects(repo)
    await add_project(repo, db_conn, "Gone Heights", status="SOLD_OUT")
    abel = await contactable(repo, "soldout", telegram=7_100_002)
    text, buttons = await service.handle_text(abel, "Book a visit to Gone Heights")
    assert "sold out" in text and buttons == []

    await add_project(repo, db_conn)
    service._names = (0.0, [])
    await service.handle_text(abel, "Book visit to Central Tower on 2026-11-02 14:00")  # inside the 24 hour reminder window
    proactive, _, _ = make_proactive(repo, services=services, broker_telegram_chat_id="999", now=lambda: NOW)
    out = await proactive.tour_reminders()
    assert out["sent"] == 1
    reminder = [m for m in services.telegram.messages if m["chat_id"] == 7_100_002 and "a reminder that your visit to Central Tower" in m["text"]]
    assert reminder and "(Noah Real Estate)" in reminder[0]["text"] and "Where: Bole" in reminder[0]["text"]


# ---------------- projects are found by meaning too ----------------

async def test_a_project_is_found_by_area_or_by_meaning_but_not_when_both_miss(repo, db_conn):
    near, far = [0.1] * 768, [0.1] * 384 + [-0.1] * 384
    garden = await add_project(repo, db_conn, "Garden Court", sub_city="Sarbet")
    other = await add_project(repo, db_conn, "Tower Place", sub_city="Lebu")
    nowhere = await add_project(repo, db_conn, "Faraway Plaza", sub_city="Ayat")
    await repo.set_project_embedding(garden["id"], near)
    await repo.set_project_embedding(other["id"], far)
    await repo.set_project_embedding(nowhere["id"], far)
    by_meaning = await repo.search_projects(sub_cities=["Bole"], embedding=near, threshold=0.65, limit=5)
    assert [p["name"] for p in by_meaning] == ["Garden Court"], "close in meaning, in another area: found; far in meaning: not"
    by_area = await repo.search_projects(sub_cities=["Lebu"], embedding=near, threshold=0.65, limit=5)
    assert [p["name"] for p in by_area] == ["Tower Place", "Garden Court"], "the area match counts even when the meaning is far, and ranks first"
    assert [p["name"] for p in await repo.search_projects(sub_cities=["Lebu"], limit=5)] == ["Tower Place"], "without a vector it is area only"


async def test_saving_a_crawl_makes_the_projects_searchable_by_meaning(repo, db_conn):
    site = Site({"https://dev.example/": "<a href='/t/'>t</a>", "https://dev.example/t/": project_page()})
    gw = FakeGateway([info()])
    service, _ = make_projects(repo, gw, site)
    await service.crawl_developer("noah", "https://dev.example/", dry_run=False)
    assert any("Central Tower - residential project in Bole" in text for text in gw.documents)
    (hit,) = await repo.search_projects(sub_cities=[], embedding=gw.vector, threshold=0.9)
    assert hit["name"] == "Central Tower" and hit["similarity"] > 0.99


# ---------------- projects next to priced results ----------------

async def test_a_project_in_the_area_is_shown_next_to_priced_options_with_its_offer_first(repo, db_conn, vector_of):
    await add_project(repo, db_conn, "Bole Plain", sub_city="Bole")
    await add_project(repo, db_conn, "Bole Offer", sub_city="Bole", promotion="5% off this month", promotion_until=(date.today() + timedelta(days=9)).isoformat())
    gw = FakeGateway([ext(max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"], search_query="3BR Bole")], vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(repo, gw), await new_buyer(repo, "alongside")
    res = await say_to(agent, buyer, "3 bedroom in Bole, 25 million")
    assert "1. Noah Bole Towers" in res.reply, "the priced options are still first"
    assert "Also worth a visit" in res.reply and "Bole Offer - Noah Real Estate" in res.reply and "Bole Plain" not in res.reply
    assert "Offer: 5% off this month" in res.reply
    texts = [b["text"] for row in res.state["quick_replies"] for b in row]
    assert "Ask the price of Bole Offer" in texts and "Book a visit to Bole Offer" in texts and "Tell me more about option 1" in texts


async def test_the_dossier_of_a_project_visit_describes_the_project_instead_of_empty_units(repo, db_conn):
    from app.dossier import build_dossier, render_html, render_text

    service, services = make_projects(repo)
    await add_project(repo, db_conn, promotion="5% off this month", promotion_until=(date.today() + timedelta(days=9)).isoformat())
    abel = await contactable(repo, "dossier", telegram=7_100_003)
    await service.handle_text(abel, "Book visit to Central Tower on 2026-11-03 10:00")
    (tour,) = [t for t in await repo.list_tours() if t["project_name"] == "Central Tower"]
    data = await build_dossier(repo, tour["id"], today=NOW.date())
    assert data["units"] == [] and data["project"]["name"] == "Central Tower" and data["project"]["price_from_etb"] is None
    text = render_text(data)
    assert "Project: Central Tower (Noah Real Estate, Bole)" in text and "126-185 sqm | 3-4 bedrooms | 21 floors" in text and "Offer: 5% off this month" in text
    assert "Price: on request" in text and "If they buy everything" not in text
    subject, html = render_html(data)
    assert "<h3>Project</h3>" in html and "Expected commission" not in html
    sent_to_broker = [m for m in services.telegram.messages if m["chat_id"] == "999" and m["text"].startswith("PRE-TOUR DOSSIER")]
    assert sent_to_broker, "the broker gets the brief as soon as the visit is booked"


# ---------------- the market sweep (language-model extraction over a list of sites) ----------------

async def test_the_sweep_reads_each_configured_site_and_one_failing_site_does_not_stop_the_others(repo, db_conn):
    site = Site({"https://a.example/": "<a href='/t/'>t</a>", "https://a.example/t/": project_page()})  # b.example is not in the dictionary: every fetch fails
    service, _ = make_projects(repo, FakeGateway([info()]), site, market_sources="noah=https://a.example/;ayat=https://b.example/")
    assert await service.sources() == [("noah", "https://a.example/"), ("ayat", "https://b.example/")]
    out = await service.market_sweep(dry_run=False)
    assert out["job"] == "market_scraper" and out["sent"] == 1 and out["skipped"] == 0
    first, second = out["details"]
    assert first["developer"] == "noah" and first["new"] == ["Central Tower"]
    assert second["developer"] == "ayat" and second["pages_read"] == 0, "the unreachable site is reported, the sweep went on"
    assert [p["name"] for p in await repo.list_projects()] == ["Central Tower"]


async def test_the_built_in_sources_cover_the_three_developers_and_a_saved_website_is_added(repo, db_conn):
    service, _ = make_projects(repo)
    slugs = [slug for slug, _ in await service.sources()]
    assert "ayat" in slugs and "noah" in slugs, "the built-in list, for developers that exist"
    await repo.create_developer(name="Zed Homes", slug="zed-homes", sales_email="s@zed.example", website_url="https://zed.example/")
    assert ("zed-homes", "https://zed.example/") in await service.sources()


async def test_the_background_sweep_reports_progress_and_refuses_to_run_twice(repo, db_conn):
    site = Site({"https://a.example/": "<a href='/t/'>t</a>", "https://a.example/t/": project_page()})
    service, _ = make_projects(repo, FakeGateway([info()]), site, market_sources="noah=https://a.example/")
    started = await service.start_market_sweep(dry_run=True)
    assert started["started"] is True and started["sources"] == 1 and started["sweep"]["running"] is True
    again = await service.start_market_sweep(dry_run=True)
    assert again["started"] is False and "already running" in again["reason"]
    await service._sweep_task
    done = service.status()["sweep"]
    assert done["running"] is False and done["last"]["sent"] == 1 and done["last"]["dry_run"] is True
    assert await repo.list_projects() == [], "a preview sweep saves nothing"


# ---------------- pictures and videos ----------------

async def test_a_broker_upload_is_posted_in_preference_to_the_scraped_picture_and_a_video_goes_as_a_video(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn)
    preview = await service.publish_ad(project["id"], dry_run=True)
    assert preview["media"] == {"kind": "photo", "url": "https://dev.example/img.jpg"} and preview["warning"] is None

    await service.add_media(project["id"], filename="front.jpg", mime="image/jpeg", data=b"JPEG")
    mine = await service.publish_ad(project["id"], dry_run=True)
    assert mine["media"]["kind"] == "photo" and mine["media"]["url"].startswith(f"projects/{project['id']}/media/")
    assert (await service.publish_ad(project["id"], dry_run=False))["posted"]
    assert services.telegram.photos[-1]["photo_bytes"] == 4, "the uploaded file, not the scraped address"

    await service.add_media(project["id"], filename="tour.mp4", mime="video/mp4", data=b"VIDEO!")
    await repo.upsert_project(await developer_id(db_conn), {**to_row(info(), Page("https://dev.example/central-tower/", BODY + " x", None)), "promo_text": "10% off", "content_hash": "v2"})
    await repo._fetch("delete from project_posts returning id")
    assert (await service.publish_ad(project["id"], dry_run=False))["posted"]
    assert services.telegram.videos[-1]["video_bytes"] == 6 and "Price: on request" in services.telegram.videos[-1]["caption"]


async def test_a_project_without_any_picture_warns_and_uploads_are_checked(repo, db_conn):
    service, _ = make_projects(repo)
    row = to_row(info("Bare Heights"), Page("https://dev.example/bare/", BODY, None))
    bare, _ = await repo.upsert_project(await developer_id(db_conn), row)
    preview = await service.publish_ad(bare["id"], dry_run=True)
    assert preview["media"] is None and "text only" in preview["warning"]
    for mime, data in (("application/pdf", b"x"), ("image/jpeg", b""), ("image/png", b"x" * (9 * 1024 * 1024)), ("video/mp4", b"x" * (21 * 1024 * 1024))):
        with pytest.raises(Exception, match="Use a JPG|empty|too large"):
            await service.add_media(bare["id"], filename="f", mime=mime, data=data)
    media = await repo.list_project_media(bare["id"])
    assert media == []


# ---------------- a price that changes after it was given ----------------

async def test_a_price_drop_is_told_to_the_buyers_who_asked_and_an_increase_only_to_the_broker(repo, db_conn):
    service, services = make_projects(repo)
    project = await add_project(repo, db_conn)
    asker = await repo.get_or_create_buyer("test:asker", 6_100_001)
    await service.handle_text(asker, "Ask the price of Central Tower")
    await service.set_price(project["id"], 20_000_000, None)
    services.telegram.messages.clear()

    drop = await service.set_price(project["id"], 18_000_000, "from the 3 bedroom")
    assert drop["change"] == "drop" and drop["buyers_told"] == 1
    told = [m for m in services.telegram.messages if m["chat_id"] == 6_100_001]
    assert len(told) == 1 and "now starts from 18M ETB (from the 3 bedroom), down from 20M ETB (10.0% lower)" in told[0]["text"]
    again = await service.set_price(project["id"], 18_000_000, "from the 3 bedroom")
    assert again["change"] is None and len([m for m in services.telegram.messages if m["chat_id"] == 6_100_001]) == 1, "the same price is not announced twice"

    services.telegram.messages.clear()
    up = await service.set_price(project["id"], 21_000_000, None)
    assert up["change"] == "increase" and up["buyers_told"] == 0
    assert [m["chat_id"] for m in services.telegram.messages] == ["999"] and "Price increase: Central Tower" in services.telegram.messages[0]["text"]
    assert "1 buyer had been given the earlier price" in services.telegram.messages[0]["text"]
