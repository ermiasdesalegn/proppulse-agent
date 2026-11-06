"""Plans cap how many buyer messages an agency's assistant answers per day; the cap is per agency and never loses a message."""
import uuid

import pytest

from app.quota import PLAN_DAILY_JOBS, PLAN_DAILY_MESSAGES, PLAN_MONTHLY_BUDGET_USD, daily_limit, exceeded, monthly_budget, over_budget, parse_prices, spend_usd
from app.tenancy import use_tenant
from tests.test_channels_api import HOOK, TOKEN_A, TOKEN_B, agency, bearer, connect, update, world  # noqa: F401


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
    body = r.json()
    assert {k: body[k] for k in ("plan", "daily_message_limit", "buyer_messages_today", "model_calls_today", "remaining", "over_limit")} == {
        "plan": "free", "daily_message_limit": 100, "buyer_messages_today": 7, "model_calls_today": 0, "remaining": 93, "over_limit": False}
    assert body["month_spend_usd"] == 0 and body["monthly_budget_usd"] == 2.0 and body["over_budget"] is False
    assert body["daily_job_limit"] == 300 and body["jobs_today"] == 0 and body["prices_as_of"]


# ---------------- money: the monthly budget ----------------


async def record_calls(db_conn, org, model, n, input_tokens, output_tokens):
    await db_conn.execute(
        "insert into model_calls (org_id, operation, lane, model, outcome, latency_ms, input_tokens, output_tokens) "
        "select %s, 'generate', 'text', %s, 'ok', 100, %s, %s from generate_series(1, %s)", (org, model, input_tokens, output_tokens, n))


def test_prices_turn_recorded_tokens_into_money_and_a_typo_does_not_stop_anything():
    prices = parse_prices("default=0.30/2.50, gemini-2.5-flash=0.30/2.50 ,cheap=0.10/0.40,broken,x=1/y,=1/2")
    assert prices == {"default": (0.30, 2.50), "gemini-2.5-flash": (0.30, 2.50), "cheap": (0.10, 0.40)}
    rows = [{"model": "cheap", "input_tokens": 2_000_000, "output_tokens": 500_000}, {"model": "unknown", "input_tokens": 1_000_000, "output_tokens": 0},
            {"model": None, "input_tokens": 0, "output_tokens": 1_000_000}]
    assert spend_usd(rows, prices) == pytest.approx(2 * 0.10 + 0.5 * 0.40 + 0.30 + 2.50)
    assert spend_usd(rows, {}) == 0.0


def test_budgets_and_job_numbers_grow_with_the_plan():
    assert PLAN_MONTHLY_BUDGET_USD["free"] < PLAN_MONTHLY_BUDGET_USD["pro"] < PLAN_MONTHLY_BUDGET_USD["business"]
    assert PLAN_DAILY_JOBS["free"] < PLAN_DAILY_JOBS["pro"] < PLAN_DAILY_JOBS["business"]
    assert monthly_budget("nonsense") == monthly_budget("free")
    assert not over_budget("free", 1.99) and over_budget("free", 2.0)


async def test_an_agency_at_its_budget_is_told_the_broker_will_reply_and_another_agency_is_unaffected(world, db_conn):  # noqa: F811
    a, b = uuid.uuid4(), uuid.uuid4()
    org_a, slug_a = await agency(db_conn, "spender", (a, "owner"))
    org_b, slug_b = await agency(db_conn, "thrifty", (b, "owner"))
    secret_a, secret_b = await connect(world, a, TOKEN_A), await connect(world, b, TOKEN_B)
    await record_calls(db_conn, org_a, "gemini-2.5-flash", 40, 20_000, 2_000)  # 40 x (0.006 + 0.005) = 0.44 dollars
    assert not over_budget("free", 0.44)
    await record_calls(db_conn, org_a, "gemini-2.5-flash", 150, 20_000, 2_000)  # 190 calls: 2.09 dollars, over the free plan's 2
    await record_calls(db_conn, org_b, "gemini-2.5-flash", 5, 20_000, 2_000)

    await world.client.post(f"/api/telegram/webhook/{slug_a}", json=update(1, text="I need a flat"), headers={HOOK: secret_a})
    await world.handler.drain()
    await world.client.post(f"/api/telegram/webhook/{slug_b}", json=update(1, text="hello"), headers={HOOK: secret_b})
    await world.handler.drain()
    assert "budget for this month" in world.sent_by(TOKEN_A)[0]["text"]
    assert "budget for this month" not in world.sent_by(TOKEN_B)[0]["text"]
    with use_tenant(org_a):
        buyer = await world.repo.get_buyer_by_thread("tg:777001")
        said = [m["content"] for m in await world.repo.list_messages(buyer["id"])]
    assert said[0] == "I need a flat" and len(said) == 2, "nothing is lost: the message waits for the broker"


async def test_the_usage_endpoint_shows_spend_against_the_budget_from_the_recorded_tokens(world, db_conn):  # noqa: F811
    admin = uuid.uuid4()
    org, _ = await agency(db_conn, "ledger", (admin, "owner"))
    await record_calls(db_conn, org, "gemini-2.5-flash", 10, 100_000, 10_000)   # 10 x (0.03 + 0.025) = 0.55
    await record_calls(db_conn, org, "gemini-3.5-flash-lite", 10, 100_000, 10_000)  # 10 x (0.01 + 0.004) = 0.14
    await db_conn.execute("insert into model_calls (org_id, operation, lane, model, outcome, latency_ms) values (%s, 'generate', 'text', 'gemini-2.5-flash', 'error', 5)", (org,))
    await db_conn.execute("insert into model_calls (org_id, operation, lane, model, outcome, latency_ms, input_tokens, output_tokens, created_at) "
                          "values (%s, 'generate', 'text', 'gemini-2.5-flash', 'ok', 5, 9000000, 9000000, now() - interval '70 days')", (org,))
    body = (await world.client.get("/api/usage", headers=bearer(admin))).json()
    assert body["month_spend_usd"] == pytest.approx(0.69) and body["monthly_budget_usd"] == 2.0 and body["over_budget"] is False
    assert body["model_calls_month"] == 21 and body["model_calls_without_tokens"] == 1, "an earlier month is not counted; a call that reported nothing is shown"
    assert "estimate" in body["prices_as_of"]
    other = uuid.uuid4()
    await agency(db_conn, "empty-ledger", (other, "owner"))
    assert (await world.client.get("/api/usage", headers=bearer(other))).json()["month_spend_usd"] == 0
