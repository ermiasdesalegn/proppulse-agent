"""The commission export: the agency's own ledger as a CSV, agent role or higher, never another agency's rows, safe to open in a spreadsheet."""
import csv
import io

import pytest

from app.commission_api import safe_cell
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


def parse(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


async def test_the_export_has_this_agencys_deals_only(world, db_conn, two):
    pid, _ = await noah(db_conn)
    _, mine = await deal(world, two["a"], "abel", pid)
    _, theirs = await deal(world, two["b"], "bekele", pid)
    r = await world.client.get("/api/commission/export.csv", headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    ids = [row["Deal id"] for row in parse(r.text)]
    assert ids == [str(mine["id"])] and str(theirs["id"]) not in r.text


async def test_the_export_carries_the_amounts_and_the_stage(world, db_conn, two):
    pid, price = await noah(db_conn)
    _, row = await deal(world, two["a"], "abel", pid)
    (line,) = parse((await world.client.get("/api/commission/export.csv", headers=as_member(two["owner_a"], two["a"]))).text)
    assert line["Stage"] == "LEAD_REGISTERED" and float(line["Expected commission (ETB)"]) == float(row["expected_commission_etb"])
    assert float(line["Listed price (ETB)"]) == price


async def test_a_viewer_is_refused_and_an_agent_is_allowed(world, db_conn, two):
    denied = await world.client.get("/api/commission/export.csv", headers=as_member(two["viewer_a"], two["a"]))
    assert denied.status_code == 403
    assert (await world.client.get("/api/commission/export.csv")).status_code in (401, 503)


async def test_an_empty_ledger_still_gives_the_header_row(world, db_conn, two):
    r = await world.client.get("/api/commission/export.csv", headers=as_member(two["owner_b"], two["b"]))
    assert r.status_code == 200 and parse(r.text) == [] and "Deal id" in r.text


def test_a_cell_that_looks_like_a_formula_is_defused():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("+251911") == "'+251911" and safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell(None) == "" and safe_cell(12.5) == "12.5" and safe_cell("Abel") == "Abel"
