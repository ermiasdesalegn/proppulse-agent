"""HTTP layer: authentication, validation and the broker/web-chat flows, on the real database (rolled back)."""
import httpx
import pytest
import pytest_asyncio

from app.main import create_app
from app.telegram_updates import TelegramUpdateHandler
from tests.agent_helpers import FakeGateway, ext, make_chat

ADMIN = {"X-Admin-Key": "test-admin-key"}
HOOK = {"X-Telegram-Bot-Api-Secret-Token": "test-hook-secret"}
SESSION = "session-abc12345"
OVERRIDES = dict(admin_api_key="test-admin-key", telegram_webhook_secret="test-hook-secret")


@pytest_asyncio.fixture
async def api(spy_repo):
    chat, services, agent = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(10)]), **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    app.state.telegram_handler = TelegramUpdateHandler(chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, app, services


def tg_update(update_id=1, text="hello", user=555001):
    return {"update_id": update_id, "message": {"chat": {"id": user, "type": "private"}, "from": {"id": user, "is_bot": False}, "text": text}}


# ---------------- authentication ----------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/buyers"), ("GET", "/api/buyers/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/buyers/00000000-0000-0000-0000-000000000000/messages"),
    ("POST", "/api/buyers/00000000-0000-0000-0000-000000000000/takeover"),
    ("POST", "/api/chat/broker-reply"), ("POST", "/api/chat/web"), ("GET", f"/api/chat/web/{SESSION}/messages"),
])
async def test_admin_endpoints_reject_missing_and_wrong_keys(api, method, path):
    client, _, _ = api
    assert (await client.request(method, path, json={})).status_code == 401
    assert (await client.request(method, path, json={}, headers={"X-Admin-Key": "wrong"})).status_code == 401


async def test_admin_endpoints_are_closed_when_no_key_is_configured(spy_repo):
    chat, _, _ = make_chat(spy_repo, FakeGateway(), admin_api_key="")
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/buyers")).status_code == 503
        assert (await c.get("/api/buyers", headers={"X-Admin-Key": ""})).status_code == 503


async def test_webhook_requires_the_telegram_secret(api):
    client, _, services = api
    assert (await client.post("/api/telegram/webhook", json=tg_update())).status_code == 403
    assert (await client.post("/api/telegram/webhook", json=tg_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})).status_code == 403
    assert services.telegram.messages == []


async def test_webhook_is_closed_when_no_secret_is_configured(spy_repo):
    chat, _, _ = make_chat(spy_repo, FakeGateway(), telegram_webhook_secret="")
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.telegram_handler = chat.settings, TelegramUpdateHandler(chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/api/telegram/webhook", json=tg_update())).status_code == 403
        assert (await c.post("/api/telegram/webhook", json=tg_update(), headers={"X-Telegram-Bot-Api-Secret-Token": ""})).status_code == 403


# ---------------- Telegram webhook ----------------

async def test_webhook_answers_200_then_the_buyer_gets_a_reply(api):
    client, app, services = api
    res = await client.post("/api/telegram/webhook", json=tg_update(1, "hello"), headers=HOOK)
    assert res.status_code == 200 and res.json() == {"ok": True}
    await app.state.telegram_handler.drain()
    assert [m["chat_id"] for m in services.telegram.messages] == [555001]


async def test_a_resent_update_does_not_reply_twice(api):
    client, app, services = api
    for _ in range(2):
        assert (await client.post("/api/telegram/webhook", json=tg_update(9, "hello"), headers=HOOK)).status_code == 200
    await app.state.telegram_handler.drain()
    assert len(services.telegram.messages) == 1


# ---------------- web chat simulator ----------------

async def test_web_chat_round_trip_and_transcript_polling(api):
    client, _, services = api
    res = await client.post("/api/chat/web", json={"session_id": SESSION, "text": "hello"}, headers=ADMIN)
    body = res.json()
    assert res.status_code == 200 and body["reply"] == "LLM-REPLY" and not body["takeover"] and body["buyer_id"]
    assert services.telegram.messages == []

    msgs = (await client.get(f"/api/chat/web/{SESSION}/messages", headers=ADMIN)).json()
    assert [m["role"] for m in msgs["messages"]] == ["buyer", "agent"] and msgs["buyer_id"] == body["buyer_id"]
    last_id = msgs["messages"][0]["id"]
    later = (await client.get(f"/api/chat/web/{SESSION}/messages", params={"after_id": last_id}, headers=ADMIN)).json()
    assert [m["role"] for m in later["messages"]] == ["agent"]


async def test_polling_an_unknown_session_is_empty_not_an_error(api):
    client, _, _ = api
    res = await client.get("/api/chat/web/never-used-123/messages", headers=ADMIN)
    assert res.status_code == 200 and res.json() == {"buyer_id": None, "takeover": False, "messages": []}


@pytest.mark.parametrize("payload", [
    {"session_id": SESSION, "text": ""}, {"session_id": SESSION, "text": "   "}, {"session_id": SESSION, "text": "x" * 2001},
    {"session_id": "short", "text": "hi"}, {"session_id": "has spaces in it", "text": "hi"}, {"session_id": "../../etc/passwd", "text": "hi"},
    {"text": "hi"},
])
async def test_web_chat_rejects_bad_input(api, payload):
    client, _, _ = api
    assert (await client.post("/api/chat/web", json=payload, headers=ADMIN)).status_code == 422


async def test_polling_rejects_a_malformed_session_id(api):
    client, _, _ = api
    assert (await client.get("/api/chat/web/bad!id/messages", headers=ADMIN)).status_code == 422


# ---------------- broker console ----------------

async def test_full_takeover_flow_over_http(api):
    client, _, _ = api
    first = (await client.post("/api/chat/web", json={"session_id": SESSION, "text": "hello"}, headers=ADMIN)).json()
    buyer_id = first["buyer_id"]

    taken = await client.post(f"/api/buyers/{buyer_id}/takeover", json={"on": True}, headers=ADMIN)
    assert taken.status_code == 200 and taken.json()["human_takeover"] is True and "preference_embedding" not in taken.json()

    held = (await client.post("/api/chat/web", json={"session_id": SESSION, "text": "can you lower it?"}, headers=ADMIN)).json()
    assert held["takeover"] is True and "will reply to you personally" in held["reply"], "the agent stays out; the buyer is told once that the broker has the chat"

    sent = await client.post("/api/chat/broker-reply", json={"buyer_id": buyer_id, "text": "Let me check with the developer."}, headers=ADMIN)
    assert sent.status_code == 200 and sent.json()["role"] == "broker"

    seen = (await client.get(f"/api/chat/web/{SESSION}/messages", headers=ADMIN)).json()["messages"]
    assert [m["role"] for m in seen] == ["buyer", "agent", "broker", "buyer", "agent", "broker"]
    assert seen[2]["source"] == "system" and seen[3]["content"] == "can you lower it?"
    assert "will reply to you personally" in seen[4]["content"], "the one-line notice that the broker has the chat"
    assert (seen[5]["content"], seen[5]["source"]) == ("Let me check with the developer.", "text")

    back = await client.post(f"/api/buyers/{buyer_id}/takeover", json={"on": False}, headers=ADMIN)
    assert back.json()["human_takeover"] is False
    refused = await client.post("/api/chat/broker-reply", json={"buyer_id": buyer_id, "text": "still there?"}, headers=ADMIN)
    assert refused.status_code == 409


async def test_broker_endpoints_handle_unknown_and_invalid_ids(api):
    client, _, _ = api
    ghost = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/buyers/{ghost}", headers=ADMIN)).status_code == 404
    assert (await client.get(f"/api/buyers/{ghost}/messages", headers=ADMIN)).status_code == 404
    assert (await client.post(f"/api/buyers/{ghost}/takeover", json={"on": True}, headers=ADMIN)).status_code == 404
    assert (await client.post("/api/chat/broker-reply", json={"buyer_id": ghost, "text": "hi"}, headers=ADMIN)).status_code == 404
    assert (await client.get("/api/buyers/not-a-uuid", headers=ADMIN)).status_code == 422
    assert (await client.post("/api/chat/broker-reply", json={"buyer_id": ghost, "text": " "}, headers=ADMIN)).status_code == 422


async def test_a_telegram_delivery_failure_is_reported_not_swallowed(api):
    from app.services.telegram import MockTelegramService, TelegramError

    class Down(MockTelegramService):
        async def send_message(self, *a, **k):
            raise TelegramError("blocked")

    client, app, services = api
    repo = app.state.repo
    buyer = await repo.get_or_create_buyer("tg:555009", 555009)
    await client.post(f"/api/buyers/{buyer['id']}/takeover", json={"on": True}, headers=ADMIN)
    app.state.chat.services.telegram = Down()
    res = await client.post("/api/chat/broker-reply", json={"buyer_id": str(buyer["id"]), "text": "hello"}, headers=ADMIN)
    assert res.status_code == 502 and "blocked" not in res.text


async def test_buyer_list_and_detail_never_expose_embeddings(api):
    client, _, _ = api
    await client.post("/api/chat/web", json={"session_id": SESSION, "text": "hello"}, headers=ADMIN)
    listing = (await client.get("/api/buyers", headers=ADMIN)).json()
    assert listing and all("preference_embedding" not in b for b in listing)
    detail = (await client.get(f"/api/buyers/{listing[0]['id']}", headers=ADMIN)).json()
    assert "preference_embedding" not in detail and detail["thread_id"]


async def test_health_reports_status_without_secrets(api):
    client, _, _ = api
    from app.config import get_settings

    res = await client.get("/health")
    assert res.json()["status"] == "ok"
    s = get_settings()
    for secret in (s.gemini_api_key, s.database_url, s.telegram_bot_token, s.supabase_service_role_key, s.admin_api_key):
        assert secret and secret not in res.text
