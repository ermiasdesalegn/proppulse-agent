"""Claims to the developer (migration 035) and the reminder for commissions pending too long.

Real database (rolled back), two agencies, the mock email service. The deal's stage is never touched by a claim.
"""
from datetime import date, timedelta

import pytest

from app.repository import LedgerEntry
from tests.jobs_helpers import app_of
from tests.test_agency_prices import as_member, buyer_of, noah
from tests.test_channels_api import agency, world  # noqa: F401
from tests.test_developer_terms import two  # noqa: F401

pytestmark = pytest.mark.usefixtures("quiet_world")

LIVE = {"resend_api_key": "re_test", "email_backend": "resend"}


def with_email(world, on=True):
    # The developer's .env may hold a real Resend key: an agency's own services are built from the runtime's settings, so blank it there or
    # the tests would call the real provider. The server's own settings below say whether email is "configured".
    world.runtime.settings = world.runtime.settings.model_copy(update={"resend_api_key": "", "email_backend": "mock"})
    app = app_of(world.client)
    s = app.state.settings
    app.state.settings = s.model_copy(update=LIVE if on else {"resend_api_key": "", "email_backend": "auto"})


async def mailbox(world, org):
    """The mock email service of one agency (each agency has its own set of services)."""
    async with world.runtime.scope(org):
        pass
    return world.runtime._cache[org][1].services.email


async def signed_deal(world, org, name, pid, stage="CONTRACT_SIGNED"):
    buyer = await buyer_of(world, org, name)
    async with world.runtime.scope(org):
        (row,) = await world.repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(pid)])
        if stage != "LEAD_REGISTERED":
            row = await world.repo.move_deal(row["id"], "CONTRACT_SIGNED", contract_value_etb=5_000_000, next_buyer_milestone_date=date.today() + timedelta(days=30))
        if stage == "COMMISSION_PENDING":
            row = await world.repo.move_deal(row["id"], "COMMISSION_PENDING")
    return row


async def test_send_claim_emails_the_developer_from_the_agency_and_records_it(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    r = await world.client.post(f"/api/ledger/{row['id']}/claim", json={"note": "Payment expected by month end"}, headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 200, r.text
    assert r.json()["claim_status"] == "SENT" and [e["action"] for e in r.json()["events"]] == ["claim_sent"]
    (mail,) = (await mailbox(world, two["a"])).sent
    assert mail["to"] == "sales@noah.example" and mail["from_name"] == "Alpha" and mail["reply_to"] == "admin@example.com"
    assert "Payment expected by month end" in mail["html"] and str(row["id"]) in mail["html"] and "5M ETB" in mail["html"]
    stage = await (await db_conn.execute("select status, claim_status from commission_ledger where id = %s", (row["id"],))).fetchone()
    assert stage["status"] == "CONTRACT_SIGNED" and stage["claim_status"] == "SENT"
    audited = await (await db_conn.execute("select action from audit_log where org_id = %s order by id desc limit 1", (two["a"],))).fetchone()
    assert audited["action"] == "claim.send"


async def test_without_an_email_provider_it_says_so_and_records_nothing(world, db_conn, two):
    with_email(world, on=False)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    r = await world.client.post(f"/api/ledger/{row['id']}/claim", json={}, headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 503 and "not set up" in r.json()["detail"]
    assert (await mailbox(world, two["a"])).sent == []
    assert (await (await db_conn.execute("select claim_status from commission_ledger where id = %s", (row["id"],))).fetchone())["claim_status"] is None
    assert (await (await db_conn.execute("select count(*) as n from commission_claim_events")).fetchone())["n"] == 0


async def test_a_failing_provider_records_nothing(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)

    async def boom(**kwargs):
        raise RuntimeError("provider down")

    (await mailbox(world, two["a"])).send = boom
    r = await world.client.post(f"/api/ledger/{row['id']}/claim", json={}, headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 502
    assert (await (await db_conn.execute("select claim_status from commission_ledger where id = %s", (row["id"],))).fetchone())["claim_status"] is None


async def test_a_claim_needs_a_signed_deal_and_a_contact(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    early = await signed_deal(world, two["a"], "abel", pid, stage="LEAD_REGISTERED")
    h = as_member(two["owner_a"], two["a"])
    assert (await world.client.post(f"/api/ledger/{early['id']}/claim", json={}, headers=h)).status_code == 409
    row = await signed_deal(world, two["a"], "bekele", pid)
    await db_conn.execute("update developers set sales_email = 'listings@x.invalid' where id = %s", (row["developer_id"],))
    assert (await world.client.post(f"/api/ledger/{row['id']}/claim", json={}, headers=h)).status_code == 422
    assert (await mailbox(world, two["a"])).sent == []


async def test_acknowledge_dispute_and_resend_follow_the_rules(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    h, url = as_member(two["owner_a"], two["a"]), f"/api/ledger/{row['id']}/claim"
    assert (await world.client.put(url, json={"status": "ACKNOWLEDGED"}, headers=h)).status_code == 409, "nothing to answer before a claim"
    await world.client.post(url, json={}, headers=h)
    disputed = await world.client.put(url, json={"status": "DISPUTED", "note": "Says the lead was theirs"}, headers=h)
    assert disputed.status_code == 200 and disputed.json()["claim_status"] == "DISPUTED"
    assert (await world.client.put(url, json={"status": "DISPUTED"}, headers=h)).status_code == 409
    assert (await world.client.post(url, json={"note": "Evidence attached"}, headers=h)).json()["claim_status"] == "SENT", "a disputed claim can be sent again"
    assert len(sent := (await mailbox(world, two["a"])).sent) == 2 and sent[0]["idempotency_key"] != sent[1]["idempotency_key"]
    ack = await world.client.put(url, json={"status": "ACKNOWLEDGED"}, headers=h)
    assert ack.json()["claim_status"] == "ACKNOWLEDGED"
    assert (await world.client.post(url, json={}, headers=h)).status_code == 409
    actions = [e["action"] for e in (await world.client.get(url, headers=h)).json()["events"]]
    assert actions == ["claim_sent", "disputed", "claim_sent", "acknowledged"], actions
    audited = [r["action"] for r in await (await db_conn.execute("select action from audit_log where org_id = %s order by id", (two["a"],))).fetchall()]
    assert audited == ["claim.send", "claim.dispute", "claim.send", "claim.acknowledge"]


async def test_roles_and_agencies(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid)
    url = f"/api/ledger/{row['id']}/claim"
    assert (await world.client.post(url, json={}, headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    assert (await world.client.get(url, headers=as_member(two["viewer_a"], two["a"]))).status_code == 200
    other = as_member(two["owner_b"], two["b"])
    assert (await world.client.post(url, json={}, headers=other)).status_code == 404, "another agency's deal does not exist for B"
    assert (await world.client.get(url, headers=other)).status_code == 404


async def test_the_reminder_flags_old_pending_commissions_once_and_tells_the_owner(world, db_conn, two):
    pid, _ = await noah(db_conn)
    old = await signed_deal(world, two["a"], "abel", pid, stage="COMMISSION_PENDING")
    fresh = await signed_deal(world, two["a"], "bekele", pid, stage="COMMISSION_PENDING")
    other = await signed_deal(world, two["b"], "chala", pid, stage="COMMISSION_PENDING")
    assert (await (await db_conn.execute("select pending_since from commission_ledger where id = %s", (old["id"],))).fetchone())["pending_since"] is not None, "the database stamps the day"
    await db_conn.execute("update commission_ledger set pending_since = now() - interval '20 days' where id in (%s, %s)", (old["id"], other["id"]))
    proactive = app_of(world.client).state.proactive
    async with world.runtime.scope(two["a"]):
        preview = await proactive.commission_overdue(dry_run=True)
        assert len(preview["details"]) == 1 and "20 days" in preview["details"][0]["would_notify_broker"]
        first = await proactive.commission_overdue()
        second = await proactive.commission_overdue()
    assert (first["sent"], second["sent"], second["skipped"]) == (1, 0, 0), "told once; a second run finds nothing new"
    flagged = {r["id"]: r["overdue_flagged_at"] for r in await (await db_conn.execute("select id, overdue_flagged_at from commission_ledger")).fetchall()}
    assert flagged[old["id"]] is not None and flagged[fresh["id"]] is None and flagged[other["id"]] is None, "B's deal waits for B's own run"
    feed = await (await db_conn.execute("select kind, org_id from proactive_events where kind = 'COMMISSION_OVERDUE'")).fetchall()
    assert [(e["kind"], e["org_id"]) for e in feed] == [("COMMISSION_OVERDUE", two["a"])]


async def test_an_acknowledged_claim_is_not_chased(world, db_conn, two):
    with_email(world)
    pid, _ = await noah(db_conn)
    row = await signed_deal(world, two["a"], "abel", pid, stage="COMMISSION_PENDING")
    h, url = as_member(two["owner_a"], two["a"]), f"/api/ledger/{row['id']}/claim"
    await world.client.post(url, json={}, headers=h)
    await world.client.put(url, json={"status": "ACKNOWLEDGED"}, headers=h)
    await db_conn.execute("update commission_ledger set pending_since = now() - interval '30 days' where id = %s", (row["id"],))
    async with world.runtime.scope(two["a"]):
        assert (await app_of(world.client).state.proactive.commission_overdue())["sent"] == 0


def test_the_job_is_scheduled_daily():
    from app.jobs import SCHEDULED
    assert SCHEDULED["commission.overdue"][1] == "day"
