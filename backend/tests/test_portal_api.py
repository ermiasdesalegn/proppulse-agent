"""The buyer-facing website API: token checks, what a buyer may see, saved homes and chat, on the real database (rolled back)."""
import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec

from app.buyer_auth import BuyerIdentity, TokenError, TokenVerifier
from app.main import create_app
from app.model3d import pack_rooms
from app.telegram_updates import TelegramUpdateHandler
from tests.agent_helpers import FakeGateway, ext, make_chat
from tests.test_agent_graph import BOLE_3BR
from tests.test_model3d import ROOMS, glb_parts

SECRET = "test-jwt-secret-that-is-long-enough-for-hs256"
OVERRIDES = dict(admin_api_key="test-admin-key", telegram_webhook_secret="test-hook-secret", supabase_jwt_secret=SECRET, supabase_url="")
USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())
GHOST = "00000000-0000-0000-0000-000000000000"
SECRET_FIELDS = {"sales_email", "commission_rate_pct", "reliability_score", "content_hash", "embedding", "developer_id", "developer_payment_plan"}


def token(user=USER_A, *, secret=SECRET, expires_in=600, **extra):
    claims = {"sub": user, "aud": "authenticated", "exp": int(time.time()) + expires_in, "email": "Abel@Example.com", "user_metadata": {"full_name": "Abel Tesfaye"}, **extra}
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(user=USER_A, **kw):
    return {"Authorization": f"Bearer {token(user, **kw)}"}


@pytest_asyncio.fixture
async def site(spy_repo):
    chat, services, _ = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(10)]), **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    app.state.telegram_handler = TelegramUpdateHandler(chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, spy_repo


# ---------------- tokens ----------------

def test_a_valid_token_names_the_buyer():
    who = TokenVerifier(jwt_secret=SECRET).verify(token())
    assert who == BuyerIdentity(user_id=USER_A, email="abel@example.com", full_name="Abel Tesfaye")
    assert who.thread_id == f"web:{USER_A}"


@pytest.mark.parametrize("bad", [
    token(secret="another-secret-that-is-also-long-enough"),
    token(expires_in=-5),
    token(aud="anon"),
    token(is_anonymous=True),
    token(user="not-a-uuid"),
    "not.a.token",
    jwt.encode({"sub": USER_A, "aud": "authenticated"}, SECRET, algorithm="HS256"),  # no expiry
])
def test_bad_tokens_are_refused(bad):
    with pytest.raises(TokenError):
        TokenVerifier(jwt_secret=SECRET).verify(bad)


def test_a_shared_secret_token_is_refused_when_no_secret_is_configured():
    with pytest.raises(TokenError):
        TokenVerifier().verify(token())


def test_the_algorithm_none_trick_does_not_work():
    forged = jwt.encode({"sub": USER_A, "aud": "authenticated", "exp": int(time.time()) + 600}, None, algorithm="none")
    with pytest.raises(TokenError):
        TokenVerifier(jwt_secret=SECRET).verify(forged)


def test_tokens_signed_with_a_private_key_are_checked_against_the_published_public_key():
    private = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())

    class Keys:
        def __init__(self, key):
            self.key = key

        def get_signing_key_from_jwt(self, _):
            return self

    verifier = TokenVerifier(supabase_url="https://abc.supabase.co", jwks_client=Keys(private.public_key()))
    claims = {"sub": USER_A, "aud": "authenticated", "exp": int(time.time()) + 600, "iss": "https://abc.supabase.co/auth/v1"}
    assert verifier.verify(jwt.encode(claims, private, algorithm="ES256")).user_id == USER_A
    with pytest.raises(TokenError):
        verifier.verify(jwt.encode(claims, other, algorithm="ES256"))  # signed by someone else
    with pytest.raises(TokenError):
        verifier.verify(jwt.encode({**claims, "iss": "https://evil.example/auth/v1"}, private, algorithm="ES256"))  # wrong project
    # A shared-secret token must not be accepted just because the public key is known.
    with pytest.raises(TokenError):
        verifier.verify(jwt.encode(claims, "x" * 40, algorithm="HS256"))


# ---------------- who may call what ----------------

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/me/chat/open"),
    ("GET", "/api/me/listings"), ("GET", f"/api/me/listings/{GHOST}"), ("GET", f"/api/me/listings/{GHOST}/preview.png"),
    ("GET", "/api/me/saved"), ("PUT", f"/api/me/saved/{GHOST}"), ("DELETE", f"/api/me/saved/{GHOST}"),
    ("POST", "/api/me/chat"), ("GET", "/api/me/chat/messages"),
])
async def test_buyer_endpoints_need_a_valid_sign_in(site, method, path):
    client, _ = site
    assert (await client.request(method, path, json={"text": "hi"})).status_code == 401
    assert (await client.request(method, path, json={"text": "hi"}, headers={"Authorization": "Bearer nonsense"})).status_code == 401
    assert (await client.request(method, path, json={"text": "hi"}, headers={"X-Admin-Key": "test-admin-key"})).status_code == 401, "the admin key is not a buyer's sign-in"
    assert (await client.request(method, path, json={"text": "hi"}, headers=auth(expires_in=-1))).status_code == 401


async def test_a_buyer_token_does_not_open_the_broker_api(site):
    client, _ = site
    assert (await client.get("/api/buyers", headers=auth())).status_code == 401
    assert (await client.get("/api/properties", headers=auth())).status_code == 401


async def test_expired_buyer_sign_ins_do_not_lock_the_broker_out(site):
    client, _ = site
    for _ in range(30):
        assert (await client.get("/api/me/listings", headers=auth(expires_in=-1))).status_code == 401
    assert (await client.get("/api/buyers", headers={"X-Admin-Key": "test-admin-key"})).status_code == 200


# ---------------- public showcase ----------------

async def test_featured_units_are_public_and_limited(site):
    client, _ = site
    res = await client.get("/api/public/featured")
    body = res.json()
    assert res.status_code == 200 and res.headers["cache-control"].startswith("public")
    assert 1 <= len(body["units"]) <= 6 and body["stats"]["units"] >= len(body["units"]) and body["stats"]["developers"] >= 1
    for unit in body["units"]:
        assert not SECRET_FIELDS & unit.keys() and unit["status"] == "AVAILABLE"
        assert "description" not in unit and "source_url" not in unit, "details are for signed-in buyers"
    assert len({u["id"] for u in body["units"]}) == len(body["units"])


async def test_the_chosen_hero_leads_the_page_without_its_detail_fields(site):
    client, _ = site
    first = (await client.get("/api/public/featured")).json()["units"][0]
    body = (await client.get(f"/api/public/featured?hero={first['id']}")).json()
    assert body["hero"]["id"] == first["id"] and not (SECRET_FIELDS | {"description", "source_url"}) & body["hero"].keys()
    assert (await client.get(f"/api/public/featured?hero={GHOST}")).json()["hero"] is None, "an unknown or sold listing falls back"
    assert (await client.get("/api/public/featured")).json()["hero"] is None


def test_a_developers_logo_is_never_used_as_a_homes_picture():
    from app.portal_api import _unit

    logo = {"id": "x", "cover_image_url": "https://noahrealestateplc.com/templates/yootheme/cache/98/NOAH%20Logo%20Options-04-987e7656.png"}
    photo = {"id": "y", "cover_image_url": "https://temerproperties.com/wp-content/uploads/2026/02/sumale-tera3.jpg"}
    assert _unit(logo)["cover"] is None and _unit(photo)["cover"] == photo["cover_image_url"]


async def test_a_cover_is_served_only_for_a_project_with_a_photo(site):
    client, _ = site
    assert (await client.get(f"/api/public/covers/{GHOST}")).status_code == 404
    assert (await client.get("/api/public/covers/not-a-uuid")).status_code == 422


# ---------------- listings ----------------

async def test_listings_filter_sort_and_never_leak_the_developer_side(site):
    client, _ = site
    everything = (await client.get("/api/me/listings", headers=auth(), params={"limit": 60})).json()
    items = everything["items"]
    assert items and everything["total"] == len(items) and all(i["status"] == "AVAILABLE" for i in items)
    assert all(not SECRET_FIELDS & i.keys() for i in items)
    assert everything["facets"]["units"] == len(items) and everything["saved"] == []

    cheap = (await client.get("/api/me/listings", headers=auth(), params={"sort": "price_asc", "limit": 60})).json()["items"]
    assert [i["price_etb"] for i in cheap] == sorted(i["price_etb"] for i in cheap)
    dear = (await client.get("/api/me/listings", headers=auth(), params={"sort": "price_desc"})).json()["items"]
    assert float(dear[0]["price_etb"]) == max(float(i["price_etb"]) for i in items)

    area = items[0]["sub_city"]
    in_area = (await client.get("/api/me/listings", headers=auth(), params={"sub_city": area.upper()})).json()
    assert in_area["total"] == sum(1 for i in items if i["sub_city"].lower() == area.lower()) and all(i["sub_city"] == area for i in in_area["items"])

    middle = sorted(float(i["price_etb"]) for i in items)[len(items) // 2]
    capped = (await client.get("/api/me/listings", headers=auth(), params={"price_max": middle})).json()["items"]
    assert capped and all(float(i["price_etb"]) <= middle for i in capped)
    big = (await client.get("/api/me/listings", headers=auth(), params={"min_bedrooms": 3})).json()["items"]
    assert all(i["bedrooms"] >= 3 for i in big)

    page = (await client.get("/api/me/listings", headers=auth(), params={"limit": 2, "offset": 1, "sort": "price_asc"})).json()
    assert [i["id"] for i in page["items"]] == [i["id"] for i in cheap[1:3]] and page["total"] == len(items)


async def test_search_text_is_matched_literally(site):
    client, _ = site
    for text in ("%", "_", "a%b", "'; drop table properties; --"):
        res = await client.get("/api/me/listings", headers=auth(), params={"q": text})
        assert res.status_code == 200 and res.json()["total"] == 0
    title = (await client.get("/api/me/listings", headers=auth())).json()["items"][0]["title"]
    assert (await client.get("/api/me/listings", headers=auth(), params={"q": title[:12].lower()})).json()["total"] >= 1


@pytest.mark.parametrize("params", [{"sort": "random"}, {"limit": 0}, {"limit": 500}, {"offset": -1}, {"min_bedrooms": -1}, {"price_min": -5}])
async def test_listing_parameters_are_validated(site, params):
    client, _ = site
    assert (await client.get("/api/me/listings", headers=auth(), params=params)).status_code == 422


async def test_listing_detail_adds_the_description_but_not_the_developer_contacts(site):
    client, _ = site
    first = (await client.get("/api/me/listings", headers=auth())).json()["items"][0]
    res = await client.get(f"/api/me/listings/{first['id']}", headers=auth())
    detail = res.json()
    assert res.status_code == 200 and detail["id"] == first["id"] and detail["saved"] is False
    assert {"description", "payment_plan", "vision_amenities", "source_url"} <= detail.keys() and not SECRET_FIELDS & detail.keys()
    assert (await client.get(f"/api/me/listings/{GHOST}", headers=auth())).status_code == 404
    assert (await client.get("/api/me/listings/not-a-uuid", headers=auth())).status_code == 422
    assert (await client.get(f"/api/me/listings/{first['id']}/preview.png", headers=auth())).status_code == 404, "no 3D layout saved for this unit"


# ---------------- saved homes ----------------

async def test_saved_homes_belong_to_one_buyer(site):
    client, _ = site
    items = (await client.get("/api/me/listings", headers=auth())).json()["items"]
    one, two = items[0]["id"], items[1]["id"]

    assert (await client.put(f"/api/me/saved/{one}", headers=auth())).json() == {"saved": True}
    assert (await client.put(f"/api/me/saved/{one}", headers=auth())).status_code == 200, "saving twice is harmless"
    await client.put(f"/api/me/saved/{two}", headers=auth())

    mine = (await client.get("/api/me/saved", headers=auth())).json()["items"]
    assert [i["id"] for i in mine] == [two, one], "newest first"
    assert (await client.get("/api/me/listings", headers=auth())).json()["saved"] == [two, one]
    assert (await client.get(f"/api/me/listings/{one}", headers=auth())).json()["saved"] is True

    assert (await client.get("/api/me/saved", headers=auth(USER_B))).json()["items"] == [], "another buyer sees nothing"
    await client.delete(f"/api/me/saved/{one}", headers=auth(USER_B))
    assert len((await client.get("/api/me/saved", headers=auth())).json()["items"]) == 2, "and cannot remove mine"

    await client.delete(f"/api/me/saved/{one}", headers=auth())
    assert [i["id"] for i in (await client.get("/api/me/saved", headers=auth())).json()["items"]] == [two]
    assert (await client.put(f"/api/me/saved/{GHOST}", headers=auth())).status_code == 404


# ---------------- chat ----------------

async def test_chat_runs_the_agent_and_fills_in_the_buyer_record(site):
    client, repo = site
    res = await client.post("/api/me/chat", json={"text": "hello"}, headers=auth())
    body = res.json()
    assert res.status_code == 200 and body["reply"] == "LLM-REPLY" and not body["takeover"]
    assert all("developer_id" not in m for m in body["active_matches"])

    buyer = await repo.get_buyer_by_thread(f"web:{USER_A}")
    assert (buyer["full_name"], buyer["email"]) == ("Abel Tesfaye", "abel@example.com")

    seen = (await client.get("/api/me/chat/messages", headers=auth())).json()
    assert [m["role"] for m in seen["messages"]] == ["buyer", "agent"] and not seen["takeover"]
    assert all(set(m) <= {"id", "role", "source", "content", "created_at", "attachments"} for m in seen["messages"])
    later = (await client.get("/api/me/chat/messages", headers=auth(), params={"after_id": seen["messages"][0]["id"]})).json()
    assert [m["role"] for m in later["messages"]] == ["agent"]


async def test_one_buyer_never_sees_another_buyers_conversation(site):
    client, _ = site
    await client.post("/api/me/chat", json={"text": "my secret budget is 20 million"}, headers=auth(USER_A))
    assert (await client.get("/api/me/chat/messages", headers=auth(USER_B))).json() == {"takeover": False, "messages": []}


async def test_chat_does_not_overwrite_what_the_agent_already_learned(site):
    client, repo = site
    buyer = await repo.get_or_create_buyer(f"web:{USER_A}")
    await repo.update_buyer(buyer["id"], full_name="Abel T.", email="abel@work.example")
    await client.post("/api/me/chat", json={"text": "hello"}, headers=auth())
    after = await repo.get_buyer_by_thread(f"web:{USER_A}")
    assert (after["full_name"], after["email"]) == ("Abel T.", "abel@work.example")


async def test_broker_takeover_reaches_the_buyer_on_the_website(site):
    client, repo = site
    await client.post("/api/me/chat", json={"text": "hello"}, headers=auth())
    buyer = await repo.get_buyer_by_thread(f"web:{USER_A}")
    admin = {"X-Admin-Key": "test-admin-key"}
    await client.post(f"/api/buyers/{buyer['id']}/takeover", json={"on": True}, headers=admin)
    held = (await client.post("/api/me/chat", json={"text": "can you lower it?"}, headers=auth())).json()
    assert held["takeover"] is True and "will reply to you personally" in held["reply"]
    await client.post("/api/chat/broker-reply", json={"buyer_id": str(buyer["id"]), "text": "Let me check."}, headers=admin)
    seen = (await client.get("/api/me/chat/messages", headers=auth())).json()
    assert seen["takeover"] is True and seen["messages"][-1]["role"] == "broker"


@pytest.mark.parametrize("payload", [{"text": ""}, {"text": "   "}, {"text": "x" * 2001}, {}])
async def test_chat_rejects_bad_input(site, payload):
    client, _ = site
    assert (await client.post("/api/me/chat", json=payload, headers=auth())).status_code == 422


# ---------------- asking about one home ----------------

async def _first_home(client):
    return (await client.get("/api/me/listings", headers=auth())).json()["items"][0]


async def test_asking_about_a_home_opens_exactly_that_home(site):
    client, _ = site
    home = await _first_home(client)
    res = await client.post("/api/me/chat/open", json={"property_id": home["id"], "action": "ask"}, headers=auth())
    body = res.json()
    assert res.status_code == 200 and home["title"] in body["reply"] and not body["error"]
    assert [m["id"] for m in body["active_matches"]] == [home["id"]] and all("developer_id" not in m for m in body["active_matches"])
    assert {b["label"] for row in body["quick_replies"] for b in row} == {"Book a visit", "Phone consultation"}

    seen = (await client.get("/api/me/chat/messages", headers=auth())).json()["messages"]
    assert [m["role"] for m in seen] == ["buyer", "agent"]
    assert seen[0]["content"] == f"Tell me about this home: {home['title']}", "the transcript says it in the website's words, not 'Opened a channel ad'"


async def test_comparing_and_booking_start_the_matching_conversation(site):
    client, _ = site
    home = await _first_home(client)
    for action in ("cmp", "book"):
        res = await client.post("/api/me/chat/open", json={"property_id": home["id"], "action": action}, headers=auth(str(uuid.uuid4())))
        assert res.status_code == 200 and res.json()["reply"], action


async def test_opening_a_home_that_does_not_exist_or_with_a_bad_action_is_refused(site):
    client, _ = site
    assert (await client.post("/api/me/chat/open", json={"property_id": GHOST}, headers=auth())).status_code == 404
    home = await _first_home(client)
    assert (await client.post("/api/me/chat/open", json={"property_id": home["id"], "action": "sell"}, headers=auth())).status_code == 422
    assert (await client.post("/api/me/chat/open", json={"property_id": "nope"}, headers=auth())).status_code == 422


async def test_opening_a_home_while_the_broker_has_the_chat_does_not_wake_the_agent(site):
    client, repo = site
    home = await _first_home(client)
    await client.post("/api/me/chat", json={"text": "hello"}, headers=auth())
    buyer = await repo.get_buyer_by_thread(f"web:{USER_A}")
    await client.post(f"/api/buyers/{buyer['id']}/takeover", json={"on": True}, headers={"X-Admin-Key": "test-admin-key"})
    res = (await client.post("/api/me/chat/open", json={"property_id": home["id"]}, headers=auth())).json()
    assert res["takeover"] is True and not res["active_matches"]


# ---------------- files the agent sends: on the website they come as attachments of the reply ----------------

BOOK_BOTH = dict(referenced_options=[1, 2], tour_start_iso="2026-11-03T10:00:00+03:00", full_name="Abel Tesfaye", phone="+251911000000", email="abel@buyer.test")


@pytest_asyncio.fixture
async def agent_site(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR), ext("book_tour", **BOOK_BOTH)], vector=await vector_of("Noah Bole Towers 3BR%"))
    chat, services, _ = make_chat(spy_repo, gw, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, spy_repo, services


async def test_a_web_buyer_gets_the_3d_view_and_model_file_as_attachments(agent_site):
    client, repo, services = agent_site
    found = (await client.post("/api/me/chat", json={"text": "3BR in Bole"}, headers=auth())).json()
    first = found["active_matches"][0]
    plain = (await client.post("/api/me/chat", json={"text": "Tell me more about option 1"}, headers=auth())).json()
    assert plain["attachments"] == [], "no layout saved, nothing is attached"

    await repo.set_layout(first["id"], pack_rooms(ROOMS, title=first["title"]).model_dump(mode="json"))
    shown = (await client.post("/api/me/chat", json={"text": "Tell me more about option 1"}, headers=auth())).json()
    assert "3D view of the layout" in shown["reply"]
    picture, model = shown["attachments"]
    assert picture["kind"] == "image" and first["title"] in picture["label"] and picture["url"].endswith("/model.png")
    assert model == {"kind": "download", "url": picture["url"].replace("model.png", "model.glb"), "label": "Download 3D model (.glb)"}
    assert not services.telegram.photos and not services.telegram.documents, "nothing goes to Telegram for a web buyer"

    stored = (await client.get("/api/me/chat/messages", headers=auth())).json()["messages"]
    assert stored[-1]["attachments"] == shown["attachments"] and all(m["attachments"] == [] for m in stored[:-1])

    png = await client.get(picture["url"], headers=auth())
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content.startswith(b"\x89PNG")
    glb = await client.get(model["url"], headers=auth())
    assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary"
    assert glb.headers["content-disposition"].startswith("attachment;") and ".glb" in glb.headers["content-disposition"]
    glb_parts(glb.content)

    for url in (picture["url"], model["url"]):
        assert (await client.get(url, headers=auth(USER_B))).status_code == 404, "another buyer cannot open these files"
        assert (await client.get(url)).status_code == 401
    assert (await client.get(picture["url"].replace("model.png", "comparison.pdf"), headers=auth())).status_code == 404, "this message has no comparison"

    await repo.delete_layout(first["id"])
    assert (await client.get(picture["url"], headers=auth())).status_code == 404, "a layout the broker removed is not served"


async def test_a_web_buyer_who_books_a_visit_gets_the_comparison_pdf(agent_site):
    client, repo, services = agent_site
    await client.post("/api/me/chat", json={"text": "3BR in Bole"}, headers=auth())
    booked = (await client.post("/api/me/chat", json={"text": "book 1 and 2 tomorrow at 10"}, headers=auth())).json()
    assert "site visit is booked" in booked["reply"]
    [pdf] = booked["attachments"]
    assert pdf["kind"] == "download" and pdf["label"] == "Download comparison (PDF)" and pdf["url"].endswith("/comparison.pdf")
    assert not services.telegram.documents

    got = await client.get(pdf["url"], headers=auth())
    assert got.status_code == 200 and got.headers["content-type"] == "application/pdf" and got.content.startswith(b"%PDF")
    assert 'filename="property-comparison.pdf"' in got.headers["content-disposition"]
    assert (await client.get(pdf["url"], headers=auth(USER_B))).status_code == 404, "another buyer cannot open the comparison"
    assert (await client.get(pdf["url"].replace("comparison.pdf", "model.glb"), headers=auth())).status_code == 404
    assert (await client.get(pdf["url"].replace("comparison.pdf", "secrets.txt"), headers=auth())).status_code == 422


async def test_a_buyer_cannot_open_files_through_a_message_id_from_another_conversation(agent_site):
    client, repo, _ = agent_site
    await client.post("/api/me/chat", json={"text": "3BR in Bole"}, headers=auth(USER_B))
    await client.post("/api/me/chat", json={"text": "book 1 and 2 tomorrow at 10"}, headers=auth(USER_B))
    theirs = (await client.get("/api/me/chat/messages", headers=auth(USER_B))).json()["messages"][-1]
    assert theirs["attachments"]
    await client.post("/api/me/chat", json={"text": "hello"}, headers=auth(USER_A))  # USER_A has a conversation of their own
    assert (await client.get(theirs["attachments"][0]["url"], headers=auth(USER_A))).status_code == 404


# ---------------- the home page's 3D viewer, voice notes and the visit-day map pin ----------------

async def test_a_homes_3d_model_is_served_only_once_the_broker_saved_its_layout(site):
    client, repo = site
    first = (await client.get("/api/me/listings", headers=auth())).json()["items"][0]
    url = f"/api/me/listings/{first['id']}/model.glb"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=auth())).status_code == 404
    await repo.set_layout(first["id"], pack_rooms(ROOMS, title=first["title"]).model_dump(mode="json"))
    res = await client.get(url, headers=auth())
    assert res.status_code == 200 and res.headers["content-type"] == "model/gltf-binary" and res.content[:4] == b"glTF"
    assert (await client.get(f"/api/me/listings/{GHOST}/model.glb", headers=auth())).status_code == 404


async def test_a_voice_note_from_the_website_is_heard_and_answered(spy_repo):
    gw = FakeGateway([ext("small_talk")], transcript="I want a 3 bedroom in Bole")
    chat, _, _ = make_chat(spy_repo, gw, **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        wav = {**auth(), "Content-Type": "audio/wav"}
        assert (await client.post("/api/me/chat/voice", content=b"RIFF....WAVE", headers={"Content-Type": "audio/wav"})).status_code == 401
        assert (await client.post("/api/me/chat/voice", content=b"x", headers={**auth(), "Content-Type": "text/plain"})).status_code == 415
        assert (await client.post("/api/me/chat/voice", content=b"x" * 3_000_001, headers=wav)).status_code == 413
        res = await client.post("/api/me/chat/voice", content=b"RIFF....WAVE", headers=wav)
        assert res.status_code == 200 and res.json()["heard"] == "I want a 3 bedroom in Bole" and res.json()["reply"]
        assert gw.audio == [b"RIFF....WAVE"]
        seen = (await client.get("/api/me/chat/messages", headers=auth())).json()["messages"]
        assert seen[0]["role"] == "buyer" and seen[0]["content"] == "🎤 I want a 3 bedroom in Bole", "the buyer sees what was heard"

        gw.transcript = "UNINTELLIGIBLE"
        assert (await client.post("/api/me/chat/voice", content=b"RIFF....WAVE", headers=wav)).status_code == 422


def test_a_visit_day_pin_becomes_a_map_link_on_the_website():
    from app.portal_api import _files

    pin = {"kind": "location", "lat": 8.99, "lng": 38.79, "title": "Bole 3BR"}
    assert _files(7, [pin]) == [{"kind": "map", "lat": 8.99, "lng": 38.79, "label": "Map: Bole 3BR"}]
    assert _files(7, [{"kind": "location", "lat": "8.99", "lng": None}]) == [], "only real coordinates make a pin"
