"""The operator's view of every agency and the controls for its plan and status (docs/PLAN_PRODUCT_AND_MONEY.md, phase 2).

Real database (rolled back), two agencies (the `world` fixture of test_channels_api); the platform acts with the admin key.
"""
import uuid

import pytest

from app.tenancy import DEFAULT_ORG_ID
from tests.test_agency_prices import as_member
from tests.test_channels_api import agency, world  # noqa: F401  (world is a fixture)

ADMIN = {"X-Admin-Key": "test-admin-key"}

pytestmark = pytest.mark.usefixtures("quiet_world")


@pytest.fixture
async def two(world, db_conn):
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    a, _ = await agency(db_conn, "alpha", (owner_a, "owner"))
    b, _ = await agency(db_conn, "beta", (owner_b, "owner"))
    return {"a": a, "b": b, "owner_a": owner_a, "owner_b": owner_b}


async def test_the_operator_sees_every_agency_with_counts_and_an_agency_cannot_ask(world, db_conn, two):
    r = await world.client.get("/api/operator/agencies", headers=ADMIN)
    assert r.status_code == 200, r.text
    by_id = {a["id"]: a for a in r.json()["agencies"]}
    assert str(DEFAULT_ORG_ID) in by_id and by_id[str(DEFAULT_ORG_ID)]["operator"] is True
    assert by_id[str(two["a"])]["plan"] == "free" and by_id[str(two["a"])]["members"] == 1 and by_id[str(two["a"])]["buyers"] == 0
    assert (await world.client.get("/api/operator/agencies", headers=as_member(two["owner_a"], two["a"]))).status_code == 403
    assert (await world.client.get("/api/operator/agencies")).status_code in (401, 403)


async def test_changing_the_plan_changes_that_agencys_limits_only_and_is_audited(world, db_conn, two):
    before = (await world.client.get("/api/usage", headers=as_member(two["owner_a"], two["a"]))).json()
    assert before["plan"] == "free"
    r = await world.client.patch(f"/api/operator/agencies/{two['a']}", json={"plan": "pro"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["plan"] == "pro", r.text
    after = (await world.client.get("/api/usage", headers=as_member(two["owner_a"], two["a"]))).json()
    assert after["plan"] == "pro" and after["daily_message_limit"] > before["daily_message_limit"]
    other = (await world.client.get("/api/usage", headers=as_member(two["owner_b"], two["b"]))).json()
    assert other["plan"] == "free" and other["daily_message_limit"] == before["daily_message_limit"], "B is untouched"
    row = await (await db_conn.execute("select action, actor_kind, detail from audit_log where org_id = %s order by seq desc limit 1", (two["a"],))).fetchone()
    assert row["action"] == "org.plan_change" and row["actor_kind"] == "platform" and row["detail"] == {"old": "free", "new": "pro"}
    assert await (await db_conn.execute("select count(*) as n from audit_log where org_id = %s", (two["b"],))).fetchone() == {"n": 0}


async def test_a_suspended_agency_is_refused_and_can_be_brought_back(world, db_conn, two):
    headers = as_member(two["owner_a"], two["a"])
    assert (await world.client.get("/api/usage", headers=headers)).status_code == 200
    r = await world.client.patch(f"/api/operator/agencies/{two['a']}", json={"status": "suspended"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert (await world.client.get("/api/usage", headers=headers)).status_code == 403
    assert (await world.client.get("/api/usage", headers=as_member(two["owner_b"], two["b"]))).status_code == 200, "B carries on"
    await world.client.patch(f"/api/operator/agencies/{two['a']}", json={"status": "active"}, headers=ADMIN)
    assert (await world.client.get("/api/usage", headers=headers)).status_code == 200


async def test_bad_requests_are_refused_and_the_operators_own_agency_cannot_be_suspended(world, db_conn, two):
    url = f"/api/operator/agencies/{two['a']}"
    assert (await world.client.patch(url, json={}, headers=ADMIN)).status_code == 422
    assert (await world.client.patch(url, json={"plan": "platinum"}, headers=ADMIN)).status_code == 422
    assert (await world.client.patch(f"/api/operator/agencies/{uuid.uuid4()}", json={"plan": "pro"}, headers=ADMIN)).status_code == 404
    assert (await world.client.patch(f"/api/operator/agencies/{DEFAULT_ORG_ID}", json={"status": "suspended"}, headers=ADMIN)).status_code == 409
    assert (await world.client.patch(url, json={"plan": "pro"}, headers=as_member(two["owner_a"], two["a"]))).status_code == 403


async def test_setting_the_same_plan_again_writes_no_second_entry(world, db_conn, two):
    await world.client.patch(f"/api/operator/agencies/{two['a']}", json={"plan": "business"}, headers=ADMIN)
    await world.client.patch(f"/api/operator/agencies/{two['a']}", json={"plan": "business"}, headers=ADMIN)
    n = await (await db_conn.execute("select count(*) as n from audit_log where org_id = %s and action = 'org.plan_change'", (two["a"],))).fetchone()
    assert n == {"n": 1}
