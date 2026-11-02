"""Plans cap how many buyer messages an agency's assistant answers per day; the cap is per agency and never loses a message."""
import uuid

from app.quota import PLAN_DAILY_MESSAGES, daily_limit, exceeded
from app.tenancy import use_tenant
from tests.test_channels_api import HOOK, TOKEN_A, agency, bearer, connect, update, world  # noqa: F401


async def fill_today(db_conn, org, n):
    """n buyer messages already received today, as the platform would see them."""
    buyer = (await (await db_conn.execute("insert into buyers_crm (org_id, thread_id) values (%s, %s) returning id", (org, f"tg:{uuid.uuid4().hex[:8]}"))).fetchone())["id"]
    await db_conn.execute("insert into chat_messages (org_id, buyer_id, role, content) select %s, %s, 'buyer', 'hi ' || g from generate_series(1, %s) g", (org, buyer, n))


def test_the_limits_grow_with_the_plan_and_an_unknown_plan_gets_the_smallest():
    assert PLAN_DAILY_MESSAGES["free"] < PLAN_DAILY_MESSAGES["pro"] < PLAN_DAILY_MESSAGES["business"]
    assert daily_limit("nonsense") == daily_limit("free") == daily_limit(None)


def test_the_first_message_over_the_limit_is_the_first_one_refused():
    limit = daily_limit("free")
    assert not exceeded({"plan": "free", "buyer_messages": limit}) and exceeded({"plan": "free", "buyer_messages": limit + 1})


async def test_over_the_limit_the_buyer_is_told_and_the_message_is_kept(world, db_conn):  # noqa: F811
    admin = uuid.uuid4()
    org, slug = await agency(db_conn, "capped", (admin, "owner"))
    secret = await connect(world, admin, TOKEN_A)
    await fill_today(db_conn, org, daily_limit("free"))
    await world.client.post(f"/api/telegram/webhook/{slug}", json=update(1, text="I need a flat"), headers={HOOK: secret})
    await world.handler.drain()
    assert "daily limit" in world.sent_by(TOKEN_A)[0]["text"]
    with use_tenant(org):
        buyer = await world.repo.get_buyer_by_thread("tg:777001")
        said = [m["content"] for m in await world.repo.list_messages(buyer["id"])]
    assert said[0] == "I need a flat" and len(said) == 2, "the message is in the broker's inbox, next to the notice"


async def test_a_bigger_plan_is_not_capped_at_the_same_volume(world, db_conn):  # noqa: F811
    admin = uuid.uuid4()
    org, slug = await agency(db_conn, "pro-agency", (admin, "owner"))
    await db_conn.execute("update organizations set plan = 'pro' where id = %s", (org,))
    secret = await connect(world, admin, TOKEN_A)
    await fill_today(db_conn, org, daily_limit("free") + 50)
    await world.client.post(f"/api/telegram/webhook/{slug}", json=update(1, text="hello"), headers={HOOK: secret})
    await world.handler.drain()
    assert "daily limit" not in world.sent_by(TOKEN_A)[0]["text"]


async def test_one_agency_reaching_its_limit_does_not_affect_another(world, db_conn):  # noqa: F811
    a, b = uuid.uuid4(), uuid.uuid4()
    org_a, _ = await agency(db_conn, "noisy", (a, "owner"))
    org_b, slug_b = await agency(db_conn, "quiet", (b, "owner"))
    secret_b = await connect(world, b, TOKEN_A)
    await fill_today(db_conn, org_a, daily_limit("free") * 3)
    await world.client.post(f"/api/telegram/webhook/{slug_b}", json=update(1, text="hello"), headers={HOOK: secret_b})
    await world.handler.drain()
    assert "daily limit" not in world.sent_by(TOKEN_A)[0]["text"]


async def test_the_usage_endpoint_shows_the_plan_and_what_is_left(world, db_conn):  # noqa: F811
    admin, viewer = uuid.uuid4(), uuid.uuid4()
    org, _ = await agency(db_conn, "meter", (admin, "owner"), (viewer, "viewer"))
    await fill_today(db_conn, org, 7)
    r = await world.client.get("/api/usage", headers=bearer(viewer))
    assert r.status_code == 200
    assert r.json() == {"plan": "free", "daily_message_limit": 100, "buyer_messages_today": 7, "model_calls_today": 0, "remaining": 93, "over_limit": False}
