"""The demo billing flow (migration 038): upgrade on a mock checkout, the plan and its quotas change at once, one receipt, one platform fee per
paid deal, nobody sees another agency's billing. Nothing moves real money.

Real database (rolled back), two agencies (`two` has an owner, an admin and a viewer in A and an owner in B).
"""
import pytest

from tests.test_agency_prices import as_member, noah
from tests.test_channels_api import agency, world  # noqa: F401
from tests.test_commission_claims import signed_deal
from tests.test_developer_terms import two  # noqa: F401

pytestmark = pytest.mark.usefixtures("quiet_world")

ADMIN = {"X-Admin-Key": "test-admin-key"}


def owner(two, key="a"):
    return as_member(two[f"owner_{key}"], two[key])


async def test_billing_shows_plan_prices_usage_and_says_it_is_a_demo(world, db_conn, two):
    r = await world.client.get("/api/billing", headers=owner(two))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "demo" and "no real money" in body["demo_notice"]
    assert body["plan"] == "free"
    assert {p["plan"]: p["price_etb"] for p in body["plans"]} == {"free": 0, "pro": 2500, "business": 9000}
    assert {p["plan"]: p["can_upgrade"] for p in body["plans"]} == {"free": False, "pro": True, "business": True}
    assert body["usage"]["daily_message_limit"] == 100 and body["invoices"] == [] and body["fees"] == []
    admin = await world.client.get("/api/billing", headers=as_member(two["admin_a"], two["a"]))
    assert admin.status_code == 200, "an admin may look"


async def test_paying_changes_the_plan_and_the_quotas_at_once_with_a_receipt_and_an_audit_entry(world, db_conn, two):
    before = (await world.client.get("/api/usage", headers=owner(two))).json()
    r = await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=owner(two))
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "pro" and [i["amount_etb"] for i in r.json()["invoices"]] == [2500] and r.json()["invoices"][0]["demo"] is True
    assert r.json()["invoices"][0]["number"].startswith("DEMO-")
    after = (await world.client.get("/api/usage", headers=owner(two))).json()
    assert after["plan"] == "pro" and after["daily_message_limit"] > before["daily_message_limit"] and after["daily_job_limit"] > before["daily_job_limit"]
    row = await (await db_conn.execute("select action, detail from audit_log where org_id = %s order by seq desc limit 1", (two["a"],))).fetchone()
    assert row["action"] == "billing.upgrade" and row["detail"]["old"] == "free" and row["detail"]["new"] == "pro" and row["detail"]["demo"] is True
    assert (await world.client.get("/api/usage", headers=owner(two, "b"))).json()["plan"] == "free", "B is untouched"


async def test_only_a_higher_plan_can_be_bought_and_a_second_click_makes_no_second_receipt(world, db_conn, two):
    assert (await world.client.post("/api/billing/upgrade", json={"plan": "business"}, headers=owner(two))).status_code == 200
    again = await world.client.post("/api/billing/upgrade", json={"plan": "business"}, headers=owner(two))
    lower = await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=owner(two))
    assert again.status_code == 409 and lower.status_code == 409
    assert (await world.client.post("/api/billing/upgrade", json={"plan": "free"}, headers=owner(two))).status_code == 422
    assert (await world.client.get("/api/billing", headers=owner(two))).json()["invoices_total_etb"] == 9000


async def test_cancelling_the_checkout_changes_nothing(world, db_conn, two):
    # Cancel is only a link back to the billing page: it sends nothing, so the plan, receipts and audit trail are as they were.
    body = (await world.client.get("/api/billing", headers=owner(two))).json()
    assert body["plan"] == "free" and body["invoices"] == []
    assert await (await db_conn.execute("select count(*) as n from invoices")).fetchone() == {"n": 0}
    assert await (await db_conn.execute("select count(*) as n from audit_log where org_id = %s", (two["a"],))).fetchone() == {"n": 0}


async def test_a_viewer_and_an_agent_may_not_look_and_only_an_owner_may_buy(world, db_conn, two):
    assert (await world.client.get("/api/billing", headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    assert (await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    assert (await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=as_member(two["admin_a"], two["a"]))).status_code == 403
    assert (await world.client.get("/api/usage", headers=owner(two))).json()["plan"] == "free"


async def test_one_agency_cannot_see_anothers_receipts_or_fees(world, db_conn, two):
    await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=owner(two))
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    async with world.runtime.scope(two["a"]):
        await world.repo.move_deal(row["id"], "COMMISSION_PAID")
    mine = (await world.client.get("/api/billing", headers=owner(two))).json()
    theirs = (await world.client.get("/api/billing", headers=owner(two, "b"))).json()
    assert len(mine["invoices"]) == 1 and len(mine["fees"]) == 1
    assert theirs["invoices"] == [] and theirs["fees"] == [] and theirs["fees_total_etb"] == 0


async def test_a_fee_is_written_exactly_once_when_a_deal_is_paid_at_the_plan_percentage(world, db_conn, two):
    await world.client.post("/api/billing/upgrade", json={"plan": "pro"}, headers=owner(two))  # pro keeps 5%
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    expected = float(row["expected_commission_etb"])
    async with world.runtime.scope(two["a"]):
        await world.repo.move_deal(row["id"], "COMMISSION_PENDING")
        assert (await world.client.get("/api/billing", headers=owner(two))).json()["fees"] == [], "nothing before it is paid"
        await world.repo.move_deal(row["id"], "COMMISSION_PAID")
        await world.repo.move_deal(row["id"], "COMMISSION_PENDING")  # moved back and paid again: still one fee
        await world.repo.move_deal(row["id"], "COMMISSION_PAID")
    fees = (await world.client.get("/api/billing", headers=owner(two))).json()
    assert len(fees["fees"]) == 1
    fee = fees["fees"][0]
    assert fee["plan"] == "pro" and fee["fee_pct"] == 5 and fee["commission_etb"] == expected and fee["fee_etb"] == round(expected * 5 / 100, 2)
    assert fees["fees_total_etb"] == fee["fee_etb"]


async def test_the_operator_sees_demo_revenue_and_fees_per_agency(world, db_conn, two):
    await world.client.post("/api/billing/upgrade", json={"plan": "business"}, headers=owner(two))
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    async with world.runtime.scope(two["a"]):
        await world.repo.move_deal(row["id"], "COMMISSION_PAID")
    r = await world.client.get("/api/operator/agencies", headers=ADMIN)
    by_id = {a["id"]: a for a in r.json()["agencies"]}
    a, b = by_id[str(two["a"])], by_id[str(two["b"])]
    assert a["demo_revenue_etb"] == 9000 and a["demo_fees_etb"] == round(float(row["expected_commission_etb"]) * 2 / 100, 2)
    assert b["demo_revenue_etb"] == 0 and b["demo_fees_etb"] == 0


async def test_an_agencys_role_cannot_write_a_fee_or_receipt_of_its_own(world, db_conn, two):
    import psycopg

    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    await db_conn.execute("set local role app_user")
    await db_conn.execute("select set_config('app.org_id', %s, true)", (str(two["a"]),))
    for statement in (
        "insert into platform_fees (org_id, ledger_id, plan, fee_pct, commission_etb, fee_etb) values (current_org(), %s, 'free', 0, 1, 0)",
        "insert into invoices (org_id, from_plan, plan, amount_etb) values (current_org(), 'free', 'pro', 0)",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with db_conn.transaction():
                await db_conn.execute(statement, (row["id"],) if "%s" in statement else ())
