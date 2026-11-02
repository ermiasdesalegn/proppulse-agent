"""Per-agency prices (migration 028): the shared price stays the default; an agency's own price is the one it shows, everywhere, to its
own buyers only; and a project's waiting buyers are told by each agency, in its own scope, with its own price.

Real database (rolled back), two agencies with their own fake bots (the `world` fixture of test_channels_api).
"""
import ast
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from app import finance
from app.brochure import brochure_data
from app.dossier import build_dossier
from app.projects import ProjectService
from app.repository import LedgerEntry
from tests.agent_helpers import FakeGateway
from tests.jobs_helpers import app_of, run_queued, total
from tests.test_channels_api import TOKEN_A, TOKEN_B, agency, bearer, connect, world  # noqa: F401  (world is a fixture)
from tests.test_projects import Ads, add_project

NOAH = "Noah Bole Towers 3BR%"
ADMIN = {"X-Admin-Key": "test-admin-key"}

pytestmark = pytest.mark.usefixtures("quiet_world")


@pytest_asyncio.fixture
async def two(world, db_conn):
    """Agencies A and B, each with an owner and its own bot, and a ProjectService wired like the app's."""
    app = app_of(world.client)
    app.state.projects = ProjectService(world.repo, FakeGateway(), world.runtime.proxied_services(), app.state.settings,
                                        proactive=app.state.proactive, ads=Ads())
    a_owner, b_owner, a_viewer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a, _ = await agency(db_conn, "alpha", (a_owner, "owner"), (a_viewer, "viewer"))
    b, _ = await agency(db_conn, "beta", (b_owner, "owner"))
    await connect(world, a_owner, TOKEN_A)
    await connect(world, b_owner, TOKEN_B)
    return {"a": a, "b": b, "a_owner": a_owner, "b_owner": b_owner, "a_viewer": a_viewer, "projects": app.state.projects}


def as_member(user, org):
    return {**bearer(user), "X-Org-Id": str(org)}


_chat = iter(range(7_700_001, 7_800_000))


async def buyer_of(world, org, tag, *, budget=None, like=NOAH):
    async with world.runtime.scope(org):
        b = await world.repo.get_or_create_buyer(f"test:{tag}:{uuid.uuid4().hex[:6]}", next(_chat))
        if budget is not None:
            await world.repo.update_buyer(b["id"], max_budget_etb=budget, min_bedrooms=3, preferred_sub_cities=["Bole"], deal_stage="QUALIFIED")
            async with world.repo._conn() as conn:
                await conn.execute("update buyers_crm set preference_embedding = (select embedding from properties where title like %s limit 1) where id = %s",
                                   (like, b["id"]))
        return await world.repo.get_buyer(b["id"])


async def ask_price(world, org, projects, buyer):
    async with world.runtime.scope(org):
        await projects.handle_text(buyer, "Ask the price of Central Tower")


def told(world, token, buyer):
    return [m["text"] for m in world.sent_by(token) if m["chat_id"] == buyer["telegram_id"]]


# ---------------- the bug this feature fixes ----------------


async def test_the_operators_price_reaches_the_buyers_who_asked_through_any_agency(world, db_conn, two):
    project = await add_project(world.repo, db_conn)
    abel, bekele = await buyer_of(world, two["a"], "abel"), await buyer_of(world, two["b"], "bekele")
    await ask_price(world, two["a"], two["projects"], abel)
    await ask_price(world, two["b"], two["projects"], bekele)

    r = await world.client.put(f"/api/projects/{project['id']}/price", json={"price_from_etb": 21_000_000, "note": "starting price"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["agencies"] == 2, r.text
    done = await run_queued(world.client)
    assert {d["org_id"] for d in done} == {two["a"], two["b"]} and total(done) == 2
    assert any("21M ETB" in t for t in told(world, TOKEN_A, abel)), "A's buyer, through A's bot"
    assert any("21M ETB" in t for t in told(world, TOKEN_B, bekele)), "B's buyer, through B's bot (before: never told)"
    assert told(world, TOKEN_A, bekele) == [] and told(world, TOKEN_B, abel) == []

    await run_queued(world.client)
    assert len(told(world, TOKEN_B, bekele)) == 1


# ---------------- an agency's own project price ----------------


async def test_an_agency_price_tells_its_own_waiting_buyers_and_survives_a_shared_change(world, db_conn, two):
    project = await add_project(world.repo, db_conn)
    abel, bekele = await buyer_of(world, two["a"], "abel"), await buyer_of(world, two["b"], "bekele")
    await ask_price(world, two["a"], two["projects"], abel)
    await ask_price(world, two["b"], two["projects"], bekele)

    r = await world.client.put(f"/api/agency-prices/project/{project['id']}", json={"price_etb": 19_500_000, "note": "our deal"},
                               headers=as_member(two["a_owner"], two["a"]))
    assert r.status_code == 200 and r.json()["agency_price_etb"] == 19_500_000 and r.json()["shared_price_from_etb"] is None, r.text
    await run_queued(world.client)
    assert any("19.5M ETB (our deal)" in t for t in told(world, TOKEN_A, abel))
    assert told(world, TOKEN_B, bekele) == [], "B has no price yet, and nobody tells B's buyer A's"

    await world.client.put(f"/api/projects/{project['id']}/price", json={"price_from_etb": 22_000_000}, headers=ADMIN)
    done = {d["org_id"]: d for d in await run_queued(world.client)}
    assert done[two["a"]]["result"]["sent"] == 0 and "its own price" in done[two["a"]]["result"]["note"], "A keeps its price"
    assert any("22M ETB" in t for t in told(world, TOKEN_B, bekele))
    assert all("22M" not in t for t in told(world, TOKEN_A, abel)), "A's buyer is never contradicted"

    async with world.runtime.scope(two["a"]):
        a_view = await world.repo.get_project(project["id"])
    async with world.runtime.scope(two["b"]):
        b_view = await world.repo.get_project(project["id"])
    assert float(a_view["price_from_etb"]) == 19_500_000 and a_view["price_note"] == "our deal" and float(a_view["shared_price_from_etb"]) == 22_000_000
    assert float(b_view["price_from_etb"]) == 22_000_000 and b_view["agency_price_etb"] is None


async def test_a_lower_agency_price_is_announced_as_a_drop_to_buyers_already_told(world, db_conn, two):
    project = await add_project(world.repo, db_conn)
    abel = await buyer_of(world, two["a"], "abel")
    await ask_price(world, two["a"], two["projects"], abel)
    await world.client.put(f"/api/projects/{project['id']}/price", json={"price_from_etb": 20_000_000}, headers=ADMIN)
    await run_queued(world.client)
    await world.client.put(f"/api/agency-prices/project/{project['id']}", json={"price_etb": 18_000_000}, headers=as_member(two["a_owner"], two["a"]))
    await run_queued(world.client)
    assert any("18M ETB" in t and "down from 20M ETB" in t for t in told(world, TOKEN_A, abel))

    cleared = await world.client.delete(f"/api/agency-prices/project/{project['id']}", headers=as_member(two["a_owner"], two["a"]))
    assert cleared.status_code == 200 and cleared.json()["price_from_etb"] == 20_000_000, "back to the shared price"
    assert (await world.client.delete(f"/api/agency-prices/project/{project['id']}", headers=as_member(two["a_owner"], two["a"]))).status_code == 404


# ---------------- an agency's own unit price, on every surface ----------------


async def noah(db_conn):
    row = await (await db_conn.execute("select id, price_etb from properties where title like %s", (NOAH,))).fetchone()
    return row["id"], float(row["price_etb"])


async def test_an_agency_unit_price_is_seen_by_that_agency_only_on_every_surface(world, db_conn, two):
    pid, shared = await noah(db_conn)
    own = round(shared * 0.8, 2)
    r = await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": own}, headers=as_member(two["a_owner"], two["a"]))
    assert r.status_code == 200, r.text
    vector = [float(x) for x in (await (await db_conn.execute("select embedding::text as e from properties where id = %s", (pid,))).fetchone())["e"].strip("[]").split(",")]

    async def surfaces(org):
        async with world.runtime.scope(org):
            repo = world.repo
            found = {m.id: m.price_etb for m in await repo.search_properties(vector, max_budget_etb=None, min_bedrooms=None, sub_cities=None, threshold=0.5, count=20)}
            ranked = {m.id: m.price_etb for m in await repo.rank_properties("cheapest", max_budget_etb=None, min_bedrooms=None, sub_cities=None, count=50)}
            (row,) = await repo.get_properties([pid])
            portal, _ = await repo.portal_units(q="Noah Bole Towers", limit=50)
            on_map = {m["id"]: m["price_etb"] for m in await repo.list_properties_for_map()}
            return {
                "search": float(found[pid]), "rank": float(ranked[pid]), "card": float(row["price_etb"]), "portal": float(next(u for u in portal if u["id"] == pid)["price_etb"]),
                "map": float(on_map[pid]), "brochure": brochure_data([row], today=date.today(), broker_name="x", buyer_name=None)["units"][0]["cells"]["price"],
                "schedule": round(sum(p.amount_etb for p in finance.payment_schedule(row, date.today()))),
            }

    a, b = await surfaces(two["a"]), await surfaces(two["b"])
    assert {k: v for k, v in a.items() if k not in ("brochure", "schedule")} == dict.fromkeys(("search", "rank", "card", "portal", "map"), own)
    assert {k: v for k, v in b.items() if k not in ("brochure", "schedule")} == dict.fromkeys(("search", "rank", "card", "portal", "map"), shared)
    assert a["brochure"] == finance.fmt_etb(own) and b["brochure"] == finance.fmt_etb(shared)
    assert a["schedule"] == round(own) and b["schedule"] == round(shared), "the payment schedule adds up to the price shown"


async def test_the_budget_filter_uses_the_agencys_price(world, db_conn, two):
    pid, shared = await noah(db_conn)
    budget = shared * 0.9  # over budget at the shared price, within it at A's
    await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": shared * 0.85}, headers=as_member(two["a_owner"], two["a"]))
    abel = await buyer_of(world, two["a"], "abel", budget=budget)
    bekele = await buyer_of(world, two["b"], "bekele", budget=budget)
    vector = [float(x) for x in (await (await db_conn.execute("select embedding::text as e from properties where id = %s", (pid,))).fetchone())["e"].strip("[]").split(",")]

    async def fits(org, buyer):
        async with world.runtime.scope(org):
            forward = pid in {m.id for m in await world.repo.search_properties(vector, max_budget_etb=budget, min_bedrooms=3, sub_cities=["Bole"], threshold=0.5, count=20)}
            reverse = buyer["id"] in {r["buyer_id"] for r in await world.repo.match_buyers_for_property(pid, threshold=0.5)}
            return forward, reverse

    assert await fits(two["a"], abel) == (True, True), "A's buyer finds it, and the reverse match finds A's buyer"
    assert await fits(two["b"], bekele) == (False, False), "at the shared price it is over B's buyer's budget"


async def test_one_buyer_sees_one_number_in_the_dossier_the_ledger_and_the_card(world, db_conn, two):
    pid, shared = await noah(db_conn)
    own = 17_000_000.0
    await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": own}, headers=as_member(two["a_owner"], two["a"]))
    abel = await buyer_of(world, two["a"], "abel")
    async with world.runtime.scope(two["a"]):
        tour = await world.repo.create_tour(buyer_id=abel["id"], scheduled_start=datetime.now(UTC) + timedelta(days=1), stops=[pid], route_json=None, calendar_event_id=None)
        dossier = await build_dossier(world.repo, tour["id"], today=date.today())
        (ledger,) = await world.repo.create_ledger_rows(buyer_id=abel["id"], tour_id=tour["id"], entries=[LedgerEntry(pid)])
        (card,) = await world.repo.get_properties([pid])
        listed = [d for d in await world.repo.list_ledger() if d["id"] == ledger["id"]]
    assert dossier["units"][0]["price_etb"] == own == float(card["price_etb"]) == float(listed[0]["property_price_etb"])
    assert float(ledger["expected_commission_etb"]) == round(own * float(card["commission_rate_pct"]) / 100, 2), "commission on the agency's price"


async def test_a_price_whose_date_has_passed_is_no_longer_used(world, db_conn, two):
    pid, shared = await noah(db_conn)
    await db_conn.execute("insert into agency_prices (org_id, target_kind, target_id, price_etb, valid_until) values (%s, 'property', %s, 1000000, %s)",
                          (two["a"], pid, date.today() - timedelta(days=1)))
    async with world.runtime.scope(two["a"]):
        (row,) = await world.repo.get_properties([pid])
    assert float(row["price_etb"]) == shared and row["agency_price_etb"] is None
    past = await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": 5, "valid_until": "2020-01-01"}, headers=as_member(two["a_owner"], two["a"]))
    assert past.status_code == 422


# ---------------- who may ----------------


async def test_who_may_set_an_agency_price_and_who_sees_it(world, db_conn, two):
    pid, _ = await noah(db_conn)
    put = lambda headers: world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": 15_000_000, "note": "deal"}, headers=headers)  # noqa: E731
    assert (await put(as_member(two["a_viewer"], two["a"]))).status_code == 403
    assert (await put({})).status_code == 401
    assert (await put(as_member(two["a_owner"], two["a"]))).status_code == 200
    assert (await world.client.put(f"/api/agency-prices/property/{uuid.uuid4()}", json={"price_etb": 1}, headers=as_member(two["a_owner"], two["a"]))).status_code == 404

    mine = (await world.client.get("/api/agency-prices", headers=as_member(two["a_viewer"], two["a"]))).json()["prices"]
    theirs = (await world.client.get("/api/agency-prices", headers=as_member(two["b_owner"], two["b"]))).json()["prices"]
    assert [(p["target_id"], float(p["price_etb"])) for p in mine] == [(str(pid), 15_000_000)] and theirs == []
    trail = await (await db_conn.execute("select action, target from audit_log where org_id = %s and action like 'price.%%'", (two["a"],))).fetchall()
    assert [t["action"] for t in trail] == ["price.override_set"] and "Noah Bole Towers" in trail[0]["target"]


async def test_an_agency_role_cannot_write_another_agencys_price(world, db_conn, two):
    pid, _ = await noah(db_conn)
    async with world.runtime.scope(two["a"]):
        async with world.repo._conn() as conn:
            with pytest.raises(Exception, match="row-level security"):
                async with conn.transaction():
                    await conn.execute("insert into agency_prices (org_id, target_kind, target_id, price_etb) values (%s, 'property', %s, 1)", (two["b"], pid))


# ---------------- no price read is left behind ----------------

# Repository statements that read `properties` or `projects` with a price on purpose: writes, the ingestion's comparison with what the
# developer's site says, the agency's own list (which shows the shared price beside its own), and the channel's record of what it posted.
SHARED_PRICE_READS = {
    "update_property_price", "get_property_by_hash", "insert_property", "upsert_project", "set_project_price", "list_agency_prices", "list_channel_posts",
}
# SQL functions that read a shared price on purpose: the trigger that records an agency's price change compares its override with the
# shared price it replaces.
SHARED_PRICE_FUNCTIONS = {"record_agency_price_change"}
PRICE = re.compile(r"\bprice_(etb|from_etb)\b")
BASE_TABLE = re.compile(r"\b(from|join)\s+(properties|projects)\b")


def _statements(fn, columns):
    """Each SQL string in a method, with the class's column lists (`{self._PROPERTY_EVENT_COLUMNS}`) filled in."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            text = ast.unparse(node)
            for name, value in columns.items():
                text = text.replace("{self." + name + "}", value)
            yield text


def test_every_repository_price_read_goes_through_the_agency_views():
    tree = ast.parse(Path("app/repository.py").read_text(encoding="utf-8"))
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Repository"]
    columns = {n.targets[0].id: ast.literal_eval(n.value) for n in cls.body
               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id.endswith("_COLUMNS")}
    offenders = []
    for fn in (n for n in cls.body if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name not in SHARED_PRICE_READS):
        offenders += [fn.name for sql in _statements(fn, columns) if BASE_TABLE.search(sql) and PRICE.search(sql)]
    (portal,) = [ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_PORTAL_UNIT"]
    if BASE_TABLE.search(portal.split("left join lateral")[0]):  # the lateral join reads only a project's picture
        offenders.append("_PORTAL_UNIT")
    assert offenders == [], f"these read a shared price where the agency's should be shown: {offenders}"


async def test_every_sql_function_that_reads_a_price_uses_the_agency_views(db_conn):
    rows = await (await db_conn.execute(
        "select p.proname, p.prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' "
        "and p.prolang in (select oid from pg_language where lanname in ('sql', 'plpgsql'))")).fetchall()
    offenders = [r["proname"] for r in rows if r["proname"] not in SHARED_PRICE_FUNCTIONS and BASE_TABLE.search(r["prosrc"]) and PRICE.search(r["prosrc"])]
    assert offenders == []


# ---------------- an agency's own unit price is announced (migration 029) ----------------


async def _set_price(world, two, who, pid, price):
    r = await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": price}, headers=as_member(two[f"{who}_owner"], two[who]))
    assert r.status_code == 200, r.text


async def _clear_price(world, two, who, pid):
    r = await world.client.delete(f"/api/agency-prices/property/{pid}", headers=as_member(two[f"{who}_owner"], two[who]))
    assert r.status_code == 200, r.text


async def _alert(world, org):
    async with world.runtime.scope(org):
        return await app_of(world.client).state.proactive.reverse_rag_alerts()


async def _broker_notices(db_conn, org, kind="PRICE_INCREASE"):
    return (await (await db_conn.execute("select id from proactive_events where org_id = %s and kind = %s", (org, kind))).fetchall())


async def test_an_agency_lowering_a_unit_price_alerts_its_own_matching_buyer_once(world, db_conn, two):
    pid, shared = await noah(db_conn)
    budget = shared - 1_000_000
    abel = await buyer_of(world, two["a"], "abel", budget=budget)
    bekele = await buyer_of(world, two["b"], "bekele", budget=budget)
    assert (await _alert(world, two["a"]))["sent"] == 0, "the shared price is out of reach, nothing to say"

    await _set_price(world, two, "a", pid, shared - 2_000_000)
    first = await _alert(world, two["a"])
    assert first["sent"] == 1
    assert any("dropped from" in t for t in told(world, TOKEN_A, abel))
    assert (await _alert(world, two["a"]))["sent"] == 0, "the same drop is not announced twice"
    assert len(told(world, TOKEN_A, abel)) == 1

    assert (await _alert(world, two["b"]))["sent"] == 0
    assert told(world, TOKEN_B, bekele) == [] and told(world, TOKEN_A, bekele) == []
    own_b = await (await db_conn.execute("select count(*) as n from agency_price_history where org_id = %s", (two["b"],))).fetchone()
    assert own_b["n"] == 0


async def test_an_agency_raising_a_unit_price_tells_only_its_own_broker(world, db_conn, two):
    pid, shared = await noah(db_conn)
    await _set_price(world, two, "a", pid, shared - 2_000_000)
    await _alert(world, two["a"])
    before = len(await _broker_notices(db_conn, two["a"]))
    await _set_price(world, two, "a", pid, shared - 1_000_000)
    await _alert(world, two["a"])
    await _alert(world, two["b"])
    await _alert(world, two["a"])
    assert len(await _broker_notices(db_conn, two["a"])) == before + 1
    assert await _broker_notices(db_conn, two["b"]) == []


async def test_clearing_an_agency_price_back_to_a_higher_shared_price_is_an_increase(world, db_conn, two):
    pid, shared = await noah(db_conn)
    abel = await buyer_of(world, two["a"], "abel", budget=shared)
    await _set_price(world, two, "a", pid, shared - 3_000_000)
    await _clear_price(world, two, "a", pid)
    rows = await (await db_conn.execute(
        "select old_price_etb, new_price_etb, change_pct from agency_price_history where org_id = %s order by detected_at, new_price_etb", (two["a"],))).fetchall()
    assert [(float(r["old_price_etb"]), float(r["new_price_etb"])) for r in rows] == [(shared, shared - 3_000_000), (shared - 3_000_000, shared)]
    assert rows[0]["change_pct"] < 0 < rows[1]["change_pct"]
    await _alert(world, two["a"])
    assert len(await _broker_notices(db_conn, two["a"])) == 1, "the broker hears of the increase"
    assert told(world, TOKEN_A, abel) == [], "the drop is no longer in effect, so it is not news"


async def test_only_a_change_of_what_the_agency_shows_is_recorded(world, db_conn, two):
    pid, shared = await noah(db_conn)
    count = lambda: db_conn.execute("select count(*) as n from agency_price_history where org_id = %s", (two["a"],))  # noqa: E731
    await _set_price(world, two, "a", pid, shared)  # the same number as the shared one
    assert (await (await count()).fetchone())["n"] == 0
    await _set_price(world, two, "a", pid, shared - 500_000)
    await _set_price(world, two, "a", pid, shared - 500_000)  # no change
    assert (await (await count()).fetchone())["n"] == 1

    # an override that had already expired is not what the agency showed: replacing it is measured from the shared price
    await _clear_price(world, two, "a", pid)
    await db_conn.execute("insert into agency_prices (org_id, target_kind, target_id, price_etb, valid_until) values (%s, 'property', %s, 1000000, %s)",
                          (two["a"], pid, date.today() - timedelta(days=1)))
    n = (await (await count()).fetchone())["n"]
    await _set_price(world, two, "a", pid, shared - 700_000)
    moved = await (await db_conn.execute("select 1 from agency_price_history where org_id = %s and old_price_etb = %s and new_price_etb = %s",
                                         (two["a"], shared, shared - 700_000))).fetchall()
    assert (await (await count()).fetchone())["n"] == n + 1 and len(moved) == 1


async def test_an_agency_cannot_read_another_agencys_price_history(world, db_conn, two):
    pid, shared = await noah(db_conn)
    await _set_price(world, two, "a", pid, shared - 1_000_000)
    async with world.runtime.scope(two["b"]):
        async with world.repo._conn() as conn:
            rows = await (await conn.execute("select 1 from agency_price_history")).fetchall()
    assert rows == []
