"""Brochure, dossier, announcement webhook, Telegram buttons and documents, email attachments."""
import base64
import json
from datetime import date

import httpx
import pytest

from app.brochure import _latin, brochure_data, render_html, render_pdf
from app.chat import Inbound, inline_keyboard
from app.dossier import build_dossier, deliver_dossier
from app.dossier import render_html as dossier_html
from app.dossier import render_text as dossier_text
from app.ingest.models import ExtractedUnit, ListingExtraction
from app.main import create_app
from app.services.email import Attachment, MockEmailService, ResendEmailService
from app.services.telegram import ALLOWED_UPDATES, BotApiTelegramService
from app.telegram_updates import parse_callback, parse_update
from tests.agent_helpers import NOW, FakeGateway, ext, make_chat, make_services
from tests.test_agent_graph import BOLE_3BR
from tests.test_ingest_service import make as make_ingest

TODAY = date(2026, 11, 2)
ADMIN = {"X-Admin-Key": "test-admin-key"}
HOOK = {"X-Webhook-Secret": "hook-secret"}
OVERRIDES = dict(admin_api_key="test-admin-key", cron_secret="test-cron-secret", ingest_webhook_secret="hook-secret", broker_telegram_chat_id="555")
PNG = base64.b64encode(bytes.fromhex("89504e470d0a1a0a") + b"0" * 20).decode()


async def two_properties(repo):
    rows = await repo._fetch("select id from properties where project_name = 'Noah Bole Towers' order by price_etb desc limit 2")
    return await repo.get_properties([r["id"] for r in rows])


# ---------------- brochure ----------------

async def test_the_brochure_compares_units_side_by_side_from_stored_facts(repo):
    props = await two_properties(repo)
    data = brochure_data(props, today=TODAY, broker_name="Ermias", buyer_name="Abel")
    assert [u["title"] for u in data["units"]] == [p["title"] for p in props]
    cells = data["units"][0]["cells"]
    assert cells["price"].endswith("M ETB") and "on signing" in cells["plan"] and cells["delivery"] == props[0]["delivery_date"].strftime("%B %Y")
    schedule = data["units"][0]["schedule"]
    assert round(sum(p["amount_etb"] for p in schedule)) == round(float(props[0]["price_etb"]))


async def test_the_html_brochure_escapes_anything_that_came_from_a_scraped_page(repo):
    props = await two_properties(repo)
    props[0] = {**props[0], "title": "<script>alert(1)</script> Tower", "vision_amenities": {"tags": ["<b>big</b>"]}}
    html = render_html(brochure_data(props, today=TODAY, broker_name="<i>Broker</i>", buyer_name="A&B"))
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;big" in html and "&lt;i&gt;Broker" in html and "A&amp;B" in html


async def test_the_pdf_brochure_is_a_real_pdf_even_with_characters_the_core_fonts_lack(repo):
    props = await two_properties(repo)
    props[0] = {**props[0], "title": "Bole – Tower ሰላም", "description": "x"}
    pdf = render_pdf(brochure_data(props, today=TODAY, broker_name="Ermias", buyer_name=None))
    assert pdf is not None and pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 1500


async def test_a_single_unit_still_makes_a_brochure(repo):
    pdf = render_pdf(brochure_data((await two_properties(repo))[:1], today=TODAY, broker_name="Ermias", buyer_name="Abel"))
    assert pdf.startswith(b"%PDF")


async def test_without_the_pdf_library_the_brochure_degrades_to_html_only(repo, monkeypatch):
    import builtins

    real = builtins.__import__

    def refuse(name, *a, **k):
        if name == "fpdf":
            raise ImportError("no fpdf")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert render_pdf(brochure_data(await two_properties(repo), today=TODAY, broker_name="E", buyer_name="A")) is None


def test_latin_text_replaces_what_the_pdf_fonts_cannot_draw():
    assert _latin("a–b ’ ሰ") == "a-b ' ?"


# ---------------- dossier ----------------

async def a_tour(repo, tag="p10-doss", kind="SITE_VISIT"):
    buyer = await repo.get_or_create_buyer(f"test:{tag}")
    await repo.update_buyer(buyer["id"], full_name="Abel Tesfaye", phone="+251911000000", max_budget_etb=15_000_000, min_bedrooms=2, lead_score=62)
    await repo.add_objections(buyer["id"], ["price feels high"])
    await repo.add_message(buyer["id"], "buyer", "I only have 15M")
    await repo.add_message(buyer["id"], "agent", "noted")
    props = await two_properties(repo)
    route = {"geometry": "x", "distance_meters": 4200, "duration_seconds": 900, "bbox": [0, 0, 1, 1]} if kind == "SITE_VISIT" else None
    tour = await repo.create_tour(buyer_id=buyer["id"], scheduled_start=NOW.replace(day=3, hour=10), stops=[p["id"] for p in props] if kind == "SITE_VISIT" else [],
                                  route_json=route, calendar_event_id="evt", kind=kind)
    return buyer, tour, props


async def test_the_dossier_gathers_the_buyer_objections_units_route_and_commission(repo):
    buyer, tour, props = await a_tour(repo)
    d = await build_dossier(repo, tour["id"], today=TODAY)
    assert d["buyer"]["name"] == "Abel Tesfaye" and d["buyer"]["lead_score"] == 62 and d["objections"] == ["price feels high"]
    assert d["recent_buyer_messages"] == ["I only have 15M"], "only what the buyer said"
    assert len(d["units"]) == 2 and d["route"] == {"distance_km": 4.2, "minutes": 15}
    expected = sum(float(p["price_etb"]) * float(p["commission_rate_pct"]) / 100 for p in props)
    assert d["expected_commission_etb"] == round(expected) and d["total_value_etb"] == round(sum(float(p["price_etb"]) for p in props))
    assert d["budget_gap_etb"] > 0 and d["kind"] == "SITE_VISIT" and d["starts_at"].startswith("2026-11-03T10:00")


async def test_a_consultation_dossier_has_no_units_or_route(repo):
    buyer, tour, _ = await a_tour(repo, "p10-doss-call", "CONSULTATION")
    d = await build_dossier(repo, tour["id"], today=TODAY)
    assert d["units"] == [] and d["route"] is None and d["expected_commission_etb"] == 0
    assert "Phone consultation" in dossier_text(d) and "Units:" not in dossier_text(d)


async def test_the_dossier_text_and_html_name_what_the_broker_needs(repo):
    _, tour, _ = await a_tour(repo)
    d = await build_dossier(repo, tour["id"], today=TODAY)
    text = dossier_text(d)
    for needle in ("PRE-TOUR DOSSIER", "Abel Tesfaye", "15M ETB budget", "lead score 62/100", "Worries raised:", "- price feels high", "Commission about", "Route: 4.2 km", "above their stated budget"):
        assert needle in text, needle
    subject, html = dossier_html(d)
    assert "Abel Tesfaye" in subject and "<table" in html and "price feels high" in html


async def test_dossier_html_escapes_what_the_buyer_typed(repo):
    buyer, tour, _ = await a_tour(repo, "p10-doss-xss")
    await repo.add_objections(buyer["id"], ["<img src=x onerror=alert(1)>"])
    _, html = dossier_html(await build_dossier(repo, tour["id"], today=TODAY))
    assert "<img" not in html and "&lt;img" in html


async def test_an_unknown_tour_is_a_lookup_error(repo):
    with pytest.raises(LookupError):
        await build_dossier(repo, "00000000-0000-0000-0000-000000000000", today=TODAY)


async def test_delivery_goes_to_the_broker_on_telegram_and_email_and_is_noted(repo):
    _, tour, _ = await a_tour(repo, "p10-deliver")
    services = make_services(email=MockEmailService())
    settings = make_chat(repo, FakeGateway(), broker_telegram_chat_id="555", demo_fallback_email="broker@x.test")[0].settings
    result = await deliver_dossier(repo, services, settings, tour["id"], today=TODAY)
    assert result["sent"] == ["telegram", "email"]
    assert services.telegram.messages[0]["chat_id"] == "555" and services.email.sent[0]["to"] == "broker@x.test"
    assert (await repo.get_tour(tour["id"]))["dossier_sent_at"] is not None


async def test_delivery_with_nowhere_to_send_changes_nothing(repo):
    _, tour, _ = await a_tour(repo, "p10-deliver-none")
    services = make_services()
    settings = make_chat(repo, FakeGateway(), broker_telegram_chat_id="", demo_fallback_telegram_chat_id="", demo_fallback_email="")[0].settings
    assert (await deliver_dossier(repo, services, settings, tour["id"], today=TODAY))["sent"] == []
    assert (await repo.get_tour(tour["id"]))["dossier_sent_at"] is None


# ---------------- dossier API ----------------

async def api(repo, gateway=None, fetcher=None):
    chat, services, _ = make_chat(repo, FakeGateway(), **OVERRIDES)
    ingest, gw, _ = make_ingest(repo, gateway=gateway, fetcher=fetcher, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.ingest = chat.settings, repo, chat, ingest
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), services, gw


async def test_the_dossier_endpoints_need_the_admin_key_and_find_the_tour(spy_repo):
    _, tour, _ = await a_tour(spy_repo, "p10-doss-api")
    client, services, _ = await api(spy_repo)
    async with client:
        assert (await client.get(f"/api/tours/{tour['id']}/dossier")).status_code == 401
        got = await client.get(f"/api/tours/{tour['id']}/dossier", headers=ADMIN)
        assert got.status_code == 200 and got.json()["buyer"]["name"] == "Abel Tesfaye" and "PRE-TOUR DOSSIER" in got.json()["text"]
        assert (await client.get("/api/tours/00000000-0000-0000-0000-000000000000/dossier", headers=ADMIN)).status_code == 404
        sent = await client.post(f"/api/tours/{tour['id']}/dossier/send", headers=ADMIN)
        assert sent.status_code == 200 and "telegram" in sent.json()["sent"]
        assert services.telegram.messages[-1]["chat_id"] == "555"


# ---------------- announcement email webhook ----------------

def announcement(**over):
    return {"message_id": "<msg-1@mail.ayat.example>", "sender": "marketing@ayat.example", "subject": "New launch: Lake View",
            "text": "Ayat announces Lake View 3BR, 130 sqm, ETB 11.9M, 25% down.", **over}


def lake_view():
    return ListingExtraction(units=[ExtractedUnit(project_name="Ayat Lake View", unit_type="3BR Apartment", sub_city="Ayat", bedrooms=3, sqm=130, price_etb=11_900_000)])


async def test_the_webhook_refuses_a_wrong_or_missing_secret(spy_repo):
    client, *_ = await api(spy_repo)
    async with client:
        assert (await client.post("/api/webhooks/announcement", json=announcement())).status_code == 403
        assert (await client.post("/api/webhooks/announcement", json=announcement(), headers={"X-Webhook-Secret": "nope"})).status_code == 403
        assert (await client.post("/api/webhooks/announcement", json=announcement(), headers=ADMIN)).status_code == 403, "the admin key is not the webhook secret"
        assert await spy_repo.list_announcements() == [], "a refused request leaves no trace"


async def test_the_webhook_is_closed_when_no_secret_is_configured(spy_repo):
    client, *_ = await api(spy_repo)
    async with client:
        client._transport.app.state.settings = client._transport.app.state.settings.model_copy(update={"ingest_webhook_secret": ""})
        assert (await client.post("/api/webhooks/announcement", json=announcement(), headers={"X-Webhook-Secret": ""})).status_code == 403


async def test_an_announcement_from_a_known_developer_is_read_and_applied(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, _, gateway = await api(spy_repo, gateway=gw)
    async with client:
        res = await client.post("/api/webhooks/announcement", json=announcement(), headers=HOOK)
        assert res.status_code == 200 and res.json()["status"] == "processed" and res.json()["developer"] == "ayat" and res.json()["counts"]["created"] == 1
        assert "New launch: Lake View" in gateway.prompts("extraction")[0], "the subject line is part of what is read"
    [row] = [a for a in await spy_repo.list_announcements() if a["message_id"] == "<msg-1@mail.ayat.example>"]
    assert row["status"] == "PROCESSED" and row["developer_name"] == "Ayat Real Estate" and row["detail"]["counts"]["created"] == 1


async def test_a_redelivered_message_is_processed_once(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, _, gateway = await api(spy_repo, gateway=gw)
    async with client:
        first = await client.post("/api/webhooks/announcement", json=announcement(), headers=HOOK)
        again = await client.post("/api/webhooks/announcement", json=announcement(), headers=HOOK)
    assert first.json()["status"] == "processed" and again.json() == {"status": "duplicate"} and gateway.count("extraction") == 1


async def test_an_unknown_sender_is_recorded_and_ignored(spy_repo):
    client, _, gateway = await api(spy_repo)
    async with client:
        res = await client.post("/api/webhooks/announcement", json=announcement(sender="stranger@gmail.com"), headers=HOOK)
    assert res.json() == {"status": "ignored", "reason": "unknown sender"} and gateway.count("extraction") == 0
    [row] = [a for a in await spy_repo.list_announcements() if a["sender"] == "stranger@gmail.com"]
    assert row["status"] == "IGNORED"


async def test_any_address_at_a_developers_domain_counts_but_a_shared_domain_does_not(spy_repo):
    assert (await spy_repo.find_developer_for_sender("Press@Noah.example"))["slug"] == "noah"
    assert (await spy_repo.find_developer_for_sender("sales@gift.example"))["slug"] == "gift"
    assert await spy_repo.find_developer_for_sender("someone@gmail.com") is None
    assert await spy_repo.find_developer_for_sender("not-an-address") is None
    await spy_repo.as_platform()._fetch("update developers set sales_email = 'sales@shared.example' where slug in ('ayat', 'noah') returning id")
    assert await spy_repo.find_developer_for_sender("x@shared.example") is None, "two developers on one domain: refuse to guess"


async def test_the_developer_can_be_named_explicitly(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, *_ = await api(spy_repo, gateway=gw)
    async with client:
        res = await client.post("/api/webhooks/announcement", json=announcement(sender="agency@pr-firm.example", developer_slug="ayat"), headers=HOOK)
    assert res.json()["developer"] == "ayat"


async def test_an_image_attachment_becomes_the_units_floor_plan(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, _, gateway = await api(spy_repo, gateway=gw)
    attachments = [{"filename": "terms.pdf", "content_type": "application/pdf", "base64": "AAAA"},
                   {"filename": "plan.png", "content_type": "image/png", "base64": PNG}]
    async with client:
        res = await client.post("/api/webhooks/announcement", json=announcement(attachments=attachments), headers=HOOK)
    assert res.json()["status"] == "processed" and gateway.count("image") == 1


async def test_a_bad_attachment_does_not_stop_the_announcement(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, _, gateway = await api(spy_repo, gateway=gw)
    fake = {"filename": "plan.png", "content_type": "image/png", "base64": base64.b64encode(b"this is not a png").decode()}
    async with client:
        res = await client.post("/api/webhooks/announcement", json=announcement(attachments=[fake]), headers=HOOK)
    assert res.json()["status"] == "processed" and gateway.count("image") == 0


async def test_text_is_taken_from_html_when_the_email_has_no_plain_part(spy_repo):
    gw = FakeGateway()
    gw.listing_extraction = lake_view()
    client, _, gateway = await api(spy_repo, gateway=gw)
    async with client:
        await client.post("/api/webhooks/announcement", json=announcement(text="", html="<html><body><p>Lake View 3BR now ETB 11.9M</p><script>evil()</script></body></html>"), headers=HOOK)
    prompt = gateway.prompts("extraction")[0]
    assert "Lake View 3BR now ETB 11.9M" in prompt and "evil()" not in prompt


async def test_an_empty_email_and_a_model_outage_are_recorded_as_failures(spy_repo):
    gw = FakeGateway()
    gw.fail_listing_extraction = True
    client, *_ = await api(spy_repo, gateway=gw)
    async with client:
        empty = await client.post("/api/webhooks/announcement", json=announcement(message_id="<empty>", text="", subject=None), headers=HOOK)
        down = await client.post("/api/webhooks/announcement", json=announcement(message_id="<down>"), headers=HOOK)
    assert empty.json()["status"] == "failed" and "no text" in empty.json()["error"]
    assert down.json()["status"] == "failed" and "gemini down" not in down.text
    by_id = {a["message_id"]: a for a in await spy_repo.list_announcements()}
    assert by_id["<empty>"]["status"] == "FAILED" and by_id["<down>"]["status"] == "FAILED"


@pytest.mark.parametrize("override", [{"message_id": ""}, {"sender": "x"}, {"text": "x" * 20_001}, {"developer_slug": "BAD SLUG"}, {"attachments": [{"filename": "a", "content_type": "image/png", "base64": "A"}] * 6}])
async def test_a_malformed_webhook_body_is_rejected(spy_repo, override):
    client, *_ = await api(spy_repo)
    async with client:
        assert (await client.post("/api/webhooks/announcement", json=announcement(**override), headers=HOOK)).status_code == 422


async def test_announcements_can_be_listed_by_the_admin_only(spy_repo):
    client, *_ = await api(spy_repo)
    async with client:
        assert (await client.get("/api/announcements")).status_code == 401
        assert (await client.get("/api/announcements", headers=ADMIN)).status_code == 200


# ---------------- Telegram buttons ----------------

def callback(data="Tell me more about option 1", **over):
    base = {"id": "cb-1", "from": {"id": 42, "is_bot": False}, "message": {"chat": {"id": 42, "type": "private"}}, "data": data}
    return {**base, **over}


def test_a_button_tap_becomes_the_sentence_it_stands_for():
    inbound = parse_callback(callback())
    assert (inbound.channel, inbound.external_id, inbound.chat_id, inbound.text, inbound.callback_id) == ("telegram", "42", 42, "Tell me more about option 1", "cb-1")
    assert parse_update({"update_id": 1, "callback_query": callback()}).text == "Tell me more about option 1"


@pytest.mark.parametrize("override", [
    {"message": {"chat": {"id": 1, "type": "group"}}}, {"from": {"id": 1, "is_bot": True}}, {"from": {}}, {"data": None}, {"data": "   "}, {"message": None},
])
def test_taps_that_are_not_from_a_person_in_a_private_chat_are_ignored(override):
    assert parse_callback(callback(**override)) is None


def test_a_long_button_payload_is_cut_not_trusted():
    assert len(parse_callback(callback("x" * 5000)).text) == 200


def test_keyboards_keep_only_buttons_telegram_will_accept():
    rows = [[{"label": "A" * 60, "text": "ok"}, {"label": "too long", "text": "z" * 65}], [{"label": "none", "text": ""}]]
    kb = inline_keyboard(rows)
    assert kb == {"inline_keyboard": [[{"text": "A" * 40, "callback_data": "ok"}]]}
    assert inline_keyboard([]) is None and inline_keyboard(None) is None and inline_keyboard([[{"label": "x", "text": "ሰ" * 30}]]) is None, "64 bytes, not 64 characters"


def telegram_api(handler):
    return BotApiTelegramService("123:abc", transport=httpx.MockTransport(handler))


async def test_documents_are_uploaded_as_multipart_with_a_caption():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(url=str(req.url), type=req.headers["content-type"], body=req.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    assert await telegram_api(handler).send_document(42, "plan.pdf", b"%PDF-1.4 data", "Your comparison") == 9
    assert seen["url"].endswith("/sendDocument") and seen["type"].startswith("multipart/form-data")
    assert b"%PDF-1.4 data" in seen["body"] and b'filename="plan.pdf"' in seen["body"] and b"Your comparison" in seen["body"]


async def test_a_failed_upload_never_leaks_the_bot_token():
    def handler(req):
        raise httpx.ConnectError("boom", request=req)

    from app.services.telegram import TelegramError

    with pytest.raises(TelegramError) as err:
        await telegram_api(handler).send_document(42, "x.pdf", b"x")
    assert "123:abc" not in str(err.value)


async def test_answering_a_tap_and_asking_for_taps_use_the_right_calls():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path.rsplit("/", 1)[1], json.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "result": []})

    tg = telegram_api(handler)
    await tg.answer_callback("cb-9")
    await tg.get_updates(None, 1)
    await tg.set_webhook("https://x.example/hook", "s")
    assert calls[0] == ("answerCallbackQuery", {"callback_query_id": "cb-9", "text": ""})
    assert calls[1][1]["allowed_updates"] == ALLOWED_UPDATES and "callback_query" in ALLOWED_UPDATES
    assert calls[2][1]["allowed_updates"] == ALLOWED_UPDATES


async def test_a_tap_is_answered_handled_as_typed_and_replied_to_with_buttons(repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    chat, services, _ = make_chat(repo, gw)
    first = await chat.handle_inbound(Inbound(channel="telegram", external_id="4242", chat_id=4242, text="3BR in Bole"))
    markup = services.telegram.messages[-1]["reply_markup"]
    assert first.quick_replies and markup["inline_keyboard"][0][0] == {"text": "Details 1", "callback_data": "Tell me more about option 1"}

    out = await chat.handle_inbound(Inbound(channel="telegram", external_id="4242", chat_id=4242, text="Tell me more about option 1", callback_id="cb-77"))
    assert services.telegram.answered == [("cb-77", "")], "the button stops spinning at once"
    assert "Payment plan:" in out.reply and services.telegram.messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "Book a site visit for option 1"
    buyer = await repo.get_buyer_by_thread("tg:4242")
    assert "Tell me more about option 1" in [m["content"] for m in await repo.list_messages(buyer["id"])], "the tap shows in the transcript as what the buyer chose"


async def test_long_replies_put_the_buttons_under_the_last_part_only(repo):
    chat, services, _ = make_chat(repo, FakeGateway())
    await chat._send_telegram(1, "a" * 4000 + "b" * 100, [[{"label": "Go", "text": "go"}]])
    first, second = services.telegram.messages
    assert first["reply_markup"] is None and second["reply_markup"] == {"inline_keyboard": [[{"text": "Go", "callback_data": "go"}]]}


async def test_the_web_chat_reports_the_buttons_too(repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    chat, *_ = make_chat(repo, gw, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, repo, chat
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.post("/api/chat/web", json={"session_id": "session-btn-0001", "text": "3BR in Bole"}, headers=ADMIN)).json()
    assert body["quick_replies"][0][0]["label"] == "Details 1"


# ---------------- email attachments ----------------

async def test_resend_receives_attachments_base64_encoded_and_none_when_there_are_none():
    bodies = []

    def handler(req: httpx.Request):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "em_1"})

    svc = ResendEmailService("key", "from@x.test", transport=httpx.MockTransport(handler))
    await svc.send(to="a@b.test", subject="s", html="h", attachments=[Attachment("plan.pdf", b"%PDF-data")])
    await svc.send(to="a@b.test", subject="s", html="h")
    assert bodies[0]["attachments"] == [{"filename": "plan.pdf", "content": base64.b64encode(b"%PDF-data").decode()}] and "attachments" not in bodies[1]
