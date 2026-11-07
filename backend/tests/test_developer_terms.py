"""An agency's own commission terms with a developer (migration 034): the shared rate is the default, an agency's own rate is the one its
expected commissions, dossiers and analytics use, and no other agency sees or is changed by it.

Real database (rolled back), two agencies (the `world` fixture of test_channels_api).
"""
import uuid
from datetime import date, timedelta

import pytest

from app.repository import LedgerEntry
from tests.test_agency_prices import NOAH, as_member, buyer_of, noah
from tests.test_channels_api import agency, world  # noqa: F401  (world is a fixture)

pytestmark = pytest.mark.usefixtures("quiet_world")


async def noah_developer(db_conn):
    row = await (await db_conn.execute(
        "select d.id, d.commission_rate_pct from developers d join properties p on p.developer_id = d.id where p.title like %s", (NOAH,))).fetchone()
    return row["id"], float(row["commission_rate_pct"])


@pytest.fixture
async def two(world, db_conn):
    owner_a, owner_b, viewer_a = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    admin_a = uuid.uuid4()
    a, _ = await agency(db_conn, "alpha", (owner_a, "owner"), (admin_a, "admin"), (viewer_a, "viewer"))
    b, _ = await agency(db_conn, "beta", (owner_b, "owner"))
    return {"a": a, "b": b, "owner_a": owner_a, "owner_b": owner_b, "admin_a": admin_a, "viewer_a": viewer_a}


async def test_an_agency_rate_changes_only_that_agencys_expected_commission(world, db_conn, two):
    dev, shared = await noah_developer(db_conn)
    pid, price = await noah(db_conn)
    r = await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 5, "note": "our deal"}, headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 200, r.text
    assert r.json()["agency_rate_pct"] == 5 and r.json()["shared_rate_pct"] == shared and r.json()["effective_rate_pct"] == 5

    abel, bekele = await buyer_of(world, two["a"], "abel"), await buyer_of(world, two["b"], "bekele")
    async with world.runtime.scope(two["a"]):
        (ledger_a,) = await world.repo.create_ledger_rows(buyer_id=abel["id"], tour_id=None, entries=[LedgerEntry(pid)])
    async with world.runtime.scope(two["b"]):
        (ledger_b,) = await world.repo.create_ledger_rows(buyer_id=bekele["id"], tour_id=None, entries=[LedgerEntry(pid)])
    assert float(ledger_a["expected_commission_etb"]) == round(float(price) * 5 / 100, 2)
    assert float(ledger_b["expected_commission_etb"]) == round(float(price) * shared / 100, 2), "B keeps the shared rate"

    async with world.runtime.scope(two["a"]):
        (row_a,) = await world.repo.get_properties([pid])
    async with world.runtime.scope(two["b"]):
        (row_b,) = await world.repo.get_properties([pid])
    assert float(row_a["commission_rate_pct"]) == 5 and float(row_b["commission_rate_pct"]) == shared, "the dossier's rate follows the agency"


async def test_the_shared_rate_is_untouched_and_the_other_agency_sees_no_term(world, db_conn, two):
    dev, shared = await noah_developer(db_conn)
    await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 6}, headers=as_member(two["owner_a"], two["a"]))
    assert float((await (await db_conn.execute("select commission_rate_pct from developers where id = %s", (dev,))).fetchone())["commission_rate_pct"]) == shared
    listed = await world.client.get("/api/developer-terms", headers=as_member(two["owner_b"], two["b"]))
    mine = next(d for d in listed.json()["developers"] if d["developer_id"] == str(dev))
    assert mine["agency_rate_pct"] is None and mine["effective_rate_pct"] == shared


async def test_only_an_admin_may_set_or_clear_a_rate_and_a_viewer_may_read(world, db_conn, two):
    dev, _ = await noah_developer(db_conn)
    denied = await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 4}, headers=as_member(two["viewer_a"], two["a"]))
    assert denied.status_code == 403
    assert (await world.client.get("/api/developer-terms", headers=as_member(two["viewer_a"], two["a"]))).status_code == 200
    ok = await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 4}, headers=as_member(two["admin_a"], two["a"]))
    assert ok.status_code == 200
    assert (await world.client.delete(f"/api/developer-terms/{dev}", headers=as_member(two["viewer_a"], two["a"]))).status_code == 403


async def test_clearing_goes_back_to_the_shared_rate_and_clearing_twice_is_a_404(world, db_conn, two):
    dev, shared = await noah_developer(db_conn)
    headers = as_member(two["owner_a"], two["a"])
    await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 7}, headers=headers)
    cleared = await world.client.delete(f"/api/developer-terms/{dev}", headers=headers)
    assert cleared.status_code == 200 and cleared.json()["agency_rate_pct"] is None and cleared.json()["effective_rate_pct"] == shared
    assert (await world.client.delete(f"/api/developer-terms/{dev}", headers=headers)).status_code == 404


async def test_bad_input_is_refused(world, db_conn, two):
    dev, _ = await noah_developer(db_conn)
    headers = as_member(two["owner_a"], two["a"])
    assert (await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 101}, headers=headers)).status_code == 422
    assert (await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": -1}, headers=headers)).status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    assert (await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 4, "valid_until": past}, headers=headers)).status_code == 422
    assert (await world.client.put(f"/api/developer-terms/{uuid.uuid4()}", json={"rate_pct": 4}, headers=headers)).status_code == 404


async def test_a_term_that_has_expired_is_ignored(world, db_conn, two):
    dev, shared = await noah_developer(db_conn)
    await db_conn.execute("insert into agency_developer_terms (org_id, developer_id, rate_pct, valid_until) values (%s, %s, 9, current_date - 1)", (two["a"], dev))
    async with world.runtime.scope(two["a"]):
        rows = await world.repo.list_developer_terms()
    row = next(r for r in rows if r["developer_id"] == dev)
    assert float(row["effective_rate_pct"]) == shared and float(row["agency_rate_pct"]) == 9


async def test_changes_are_audited_without_touching_another_agencys_trail(world, db_conn, two):
    dev, _ = await noah_developer(db_conn)
    await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 5}, headers=as_member(two["owner_a"], two["a"]))
    await world.client.delete(f"/api/developer-terms/{dev}", headers=as_member(two["owner_a"], two["a"]))
    actions = [r["action"] for r in await (await db_conn.execute("select action from audit_log where org_id = %s order by seq", (two["a"],))).fetchall()]
    assert actions[-2:] == ["terms.set", "terms.clear"]
    assert await (await db_conn.execute("select count(*) as n from audit_log where org_id = %s", (two["b"],))).fetchone() == {"n": 0}
