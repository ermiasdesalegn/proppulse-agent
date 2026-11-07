"""A new agency's first five minutes (GET /api/onboarding, PATCH /api/org/name): four ticks counted from the agency's own rows, and a rename
that keeps the address. Real database (rolled back), two agencies (the `world` fixture of test_channels_api)."""
import uuid

import pytest

from tests.test_agency_prices import as_member, noah
from tests.test_channels_api import TOKEN_A, agency, world  # noqa: F401  (world is a fixture)
from tests.test_developer_terms import noah_developer

pytestmark = pytest.mark.usefixtures("quiet_world")

NONE_DONE = {"telegram": False, "price": False, "commission": False, "teammate": False}


@pytest.fixture
async def two(world, db_conn):
    """Alpha has a full team (so its teammate step is ticked from the start where a test asks for it); use `solo_two` for a lone owner."""
    owner_a, owner_b, agent_a, viewer_a, admin_a = (uuid.uuid4() for _ in range(5))
    a, slug_a = await agency(db_conn, "alpha", (owner_a, "owner"), (admin_a, "admin"), (agent_a, "agent"), (viewer_a, "viewer"))
    b, slug_b = await agency(db_conn, "beta", (owner_b, "owner"))
    return {"a": a, "b": b, "slug_a": slug_a, "slug_b": slug_b, "owner_a": owner_a, "owner_b": owner_b, "agent_a": agent_a, "viewer_a": viewer_a, "admin_a": admin_a}


@pytest.fixture
async def solo_two(world, db_conn):
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    a, slug_a = await agency(db_conn, "solo-a", (owner_a, "owner"))
    b, slug_b = await agency(db_conn, "solo-b", (owner_b, "owner"))
    return {"a": a, "b": b, "slug_a": slug_a, "slug_b": slug_b, "owner_a": owner_a, "owner_b": owner_b}


async def progress(world, user, org):
    r = await world.client.get("/api/onboarding", headers=as_member(user, org))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.pop("org_id") == str(org)
    return body


async def test_a_fresh_agency_has_nothing_ticked(world, db_conn):
    owner = uuid.uuid4()
    org, _ = await agency(db_conn, "fresh", (owner, "owner"))
    assert await progress(world, owner, org) == NONE_DONE


async def test_each_step_ticks_after_its_action(world, db_conn, solo_two):
    a, owner = solo_two["a"], solo_two["owner_a"]
    h = as_member(owner, a)
    assert (await world.client.put("/api/channels/telegram", json={"bot_token": TOKEN_A}, headers=h)).status_code == 200
    assert await progress(world, owner, a) == {**NONE_DONE, "telegram": True}

    pid, price = await noah(db_conn)
    assert (await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": float(price) - 1000}, headers=h)).status_code == 200
    assert await progress(world, owner, a) == {**NONE_DONE, "telegram": True, "price": True}

    dev, _ = await noah_developer(db_conn)
    assert (await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 4}, headers=h)).status_code == 200
    assert (await progress(world, owner, a))["commission"] is True

    assert (await world.client.post("/api/invitations", json={"email": "new@example.com", "role": "agent"}, headers=h)).status_code == 201
    assert await progress(world, owner, a) == {"telegram": True, "price": True, "commission": True, "teammate": True}


async def test_a_second_membership_counts_as_a_teammate(world, db_conn):
    owner = uuid.uuid4()
    org, _ = await agency(db_conn, "pair", (owner, "owner"), (uuid.uuid4(), "agent"))
    assert (await progress(world, owner, org))["teammate"] is True


async def test_one_agencys_progress_never_reflects_anothers(world, db_conn, solo_two):
    two = solo_two
    ha = as_member(two["owner_a"], two["a"])
    pid, price = await noah(db_conn)
    dev, _ = await noah_developer(db_conn)
    await world.client.put("/api/channels/telegram", json={"bot_token": TOKEN_A}, headers=ha)
    await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": float(price) - 1000}, headers=ha)
    await world.client.put(f"/api/developer-terms/{dev}", json={"rate_pct": 4}, headers=ha)
    await world.client.post("/api/invitations", json={"email": "x@example.com", "role": "viewer"}, headers=ha)
    assert all((await progress(world, two["owner_a"], two["a"])).values())
    assert await progress(world, two["owner_b"], two["b"]) == NONE_DONE


async def test_the_checklist_is_for_admins_only(world, two):
    for who in ("agent_a", "viewer_a"):
        r = await world.client.get("/api/onboarding", headers=as_member(two[who], two["a"]))
        assert r.status_code == 403
    assert (await world.client.get("/api/onboarding", headers=as_member(two["admin_a"], two["a"]))).status_code == 200


# ---- rename ----

async def names(db_conn, org):
    row = await (await db_conn.execute(
        "select o.name, o.slug, s.display_name from organizations o join org_settings s on s.org_id = o.id where o.id = %s", (org,))).fetchone()
    return row["name"], row["display_name"], row["slug"]


async def test_an_admin_renames_the_agency_and_the_address_stays(world, db_conn, two):
    a = two["a"]
    r = await world.client.patch("/api/org/name", json={"name": "  Bole   Homes  "}, headers=as_member(two["admin_a"], a))
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "Bole Homes"}
    assert await names(db_conn, a) == ("Bole Homes", "Bole Homes", two["slug_a"])
    mine = (await world.client.get("/api/orgs/mine", headers=as_member(two["owner_a"], a))).json()["organizations"]
    assert next(o for o in mine if o["id"] == str(a))["name"] == "Bole Homes"
    assert (await world.client.get("/api/channels", headers=as_member(two["owner_a"], a))).json()["name"] == "Bole Homes"
    trail = (await world.client.get("/api/audit", headers=as_member(two["owner_a"], a))).json()["entries"]
    entry = next(e for e in trail if e["action"] == "org.rename")
    assert entry["target"] == "Bole Homes" and entry["detail"]["old_name"] == "Alpha"


async def test_a_rename_leaves_another_agency_alone(world, db_conn, two):
    before = await names(db_conn, two["b"])
    await world.client.patch("/api/org/name", json={"name": "Renamed Alpha"}, headers=as_member(two["owner_a"], two["a"]))
    assert await names(db_conn, two["b"]) == before


async def test_agents_and_viewers_cannot_rename(world, db_conn, two):
    before = await names(db_conn, two["a"])
    for who in ("agent_a", "viewer_a"):
        r = await world.client.patch("/api/org/name", json={"name": "Hijacked"}, headers=as_member(two[who], two["a"]))
        assert r.status_code == 403
    assert await names(db_conn, two["a"]) == before


@pytest.mark.parametrize("bad", ["", " ", "x", "  y  ", "n" * 81, "   " + "n" * 81])
async def test_bad_names_are_refused(world, db_conn, two, bad):
    before = await names(db_conn, two["a"])
    r = await world.client.patch("/api/org/name", json={"name": bad}, headers=as_member(two["owner_a"], two["a"]))
    assert r.status_code == 422
    assert await names(db_conn, two["a"]) == before


async def test_the_longest_and_shortest_names_are_accepted(world, two):
    for ok in ("ab", "n" * 80):
        r = await world.client.patch("/api/org/name", json={"name": ok}, headers=as_member(two["owner_a"], two["a"]))
        assert r.status_code == 200 and r.json() == {"name": ok}
