"""Deal assignment and split (migration 036): who owns a buyer or deal, how a commission is shared, and who earned what in the export."""
import csv
import io
import uuid

import psycopg
import pytest

from app.commission_api import earnings_rows
from app.repository import LedgerEntry
from tests.test_agency_prices import as_member, buyer_of, noah
from tests.test_channels_api import agency, world  # noqa: F401
from tests.test_developer_terms import two  # noqa: F401

pytestmark = pytest.mark.usefixtures("quiet_world")


async def deal(world, org, name, pid):
    buyer = await buyer_of(world, org, name)
    async with world.runtime.scope(org):
        (row,) = await world.repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(pid)])
    return buyer, row


async def test_assigning_a_buyer_moves_its_open_deals_and_new_deals_inherit(world, db_conn, two):
    pid, _ = await noah(db_conn)
    buyer, row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    r = await world.client.put(f"/api/buyers/{buyer['id']}/assignee", json={"user_id": str(two["admin_a"])}, headers=h)
    assert r.status_code == 200 and r.json()["deals_moved"] == 1
    assert str((await (await db_conn.execute("select assigned_to from commission_ledger where id = %s", (row["id"],))).fetchone())["assigned_to"]) == str(two["admin_a"])
    async with world.runtime.scope(two["a"]):
        (second,) = await world.repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(pid)])
    assert str(second["assigned_to"]) == str(two["admin_a"])
    cleared = await world.client.put(f"/api/buyers/{buyer['id']}/assignee", json={"user_id": None}, headers=h)
    assert cleared.json()["deals_moved"] == 2


async def test_only_a_member_can_be_assigned_and_only_in_your_own_agency(world, db_conn, two):
    pid, _ = await noah(db_conn)
    buyer, row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    stranger = await world.client.put(f"/api/ledger/{row['id']}/assignee", json={"user_id": str(two["owner_b"])}, headers=h)
    assert stranger.status_code == 422
    other = as_member(two["owner_b"], two["b"])
    assert (await world.client.put(f"/api/ledger/{row['id']}/assignee", json={"user_id": str(two["owner_b"])}, headers=other)).status_code == 404
    assert (await world.client.put(f"/api/buyers/{buyer['id']}/assignee", json={"user_id": None}, headers=other)).status_code == 404
    assert (await world.client.put(f"/api/ledger/{row['id']}/assignee", json={"user_id": str(two["admin_a"])}, headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    ok = await world.client.put(f"/api/ledger/{row['id']}/assignee", json={"user_id": str(two["admin_a"])}, headers=h)
    assert ok.status_code == 200 and ok.json()["assigned_to"] == str(two["admin_a"])


async def test_a_split_must_add_up_to_100_and_is_shown_on_the_board(world, db_conn, two):
    pid, _ = await noah(db_conn)
    _, row = await deal(world, two["a"], "abel", pid)
    h, url = as_member(two["owner_a"], two["a"]), f"/api/ledger/{row['id']}/split"
    a, b = str(two["owner_a"]), str(two["admin_a"])
    assert (await world.client.put(url, json={"shares": [{"user_id": a, "percent": 60}, {"user_id": b, "percent": 30}]}, headers=h)).status_code == 422
    assert (await world.client.put(url, json={"shares": [{"user_id": a, "percent": 50}, {"user_id": a, "percent": 50}]}, headers=h)).status_code == 422
    assert (await world.client.put(url, json={"shares": [{"user_id": str(uuid.uuid4()), "percent": 100}]}, headers=h)).status_code == 422
    ok = await world.client.put(url, json={"shares": [{"user_id": a, "percent": 60}, {"user_id": b, "percent": 40}]}, headers=h)
    assert ok.status_code == 200, ok.text
    board = (await world.client.get("/api/board", headers=h)).json()
    assert [(s["user_id"], float(s["percent"])) for s in board["deals"][0]["split"]] == [(a, 60.0), (b, 40.0)]
    assert (await world.client.put(url, json={"shares": []}, headers=h)).json()["split"] == []
    actions = [r["action"] for r in await (await db_conn.execute("select action from audit_log where org_id = %s order by id", (two["a"],))).fetchall()]
    assert actions == ["deal.split", "deal.split"]


async def test_the_database_itself_refuses_a_split_that_is_not_100(world, db_conn, two):
    pid, _ = await noah(db_conn)
    _, row = await deal(world, two["a"], "abel", pid)
    async with world.runtime.scope(two["a"]):
        with pytest.raises(psycopg.errors.CheckViolation):
            await world.repo.set_split(row["id"], [(two["owner_a"], 70)])


async def test_the_earnings_export_says_who_earned_what(world, db_conn, two):
    pid, _ = await noah(db_conn)
    _, row = await deal(world, two["a"], "abel", pid)
    _, plain = await deal(world, two["a"], "bekele", pid)
    h = as_member(two["owner_a"], two["a"])
    a, b = str(two["owner_a"]), str(two["admin_a"])
    await world.client.put(f"/api/ledger/{row['id']}/split", json={"shares": [{"user_id": a, "percent": 75}, {"user_id": b, "percent": 25}]}, headers=h)
    await world.client.put(f"/api/ledger/{plain['id']}/assignee", json={"user_id": b}, headers=h)
    r = await world.client.get("/api/commission/earnings.csv", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    total = float(row["expected_commission_etb"])
    mine = {(x["Deal id"], x["Member id"]): float(x["Commission share (ETB)"]) for x in lines}
    assert mine[(str(row["id"]), a)] == round(total * 0.75, 2) and mine[(str(row["id"]), b)] == round(total * 0.25, 2)
    assert mine[(str(plain["id"]), b)] == float(plain["expected_commission_etb"])
    assert (await world.client.get("/api/commission/earnings.csv", headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    main = (await world.client.get("/api/commission/export.csv", headers=h)).text
    assert "Assigned to (member id)" in main and f"{a} 75%" in main


def test_unassigned_deals_are_shown_as_such():
    (r,) = earnings_rows([{"id": 1, "expected_commission_etb": 100, "assigned_to": None, "split": [], "status": "COMMISSION_PAID"}])
    assert r["member"] == "unassigned" and r["earned_etb"] == 100 and r["paid"] == "yes"


async def test_the_members_list_says_who_is_asking(world, db_conn, two):
    r = await world.client.get("/api/members", headers=as_member(two["admin_a"], two["a"]))
    assert r.json()["me"] == str(two["admin_a"])
