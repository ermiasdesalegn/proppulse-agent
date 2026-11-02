"""The public website's view of an agency: its card (name, colour, logo), the directory of agencies that opted in, and the same unit at
each agency's own price. Real database, rolled back; the agencies come from the `world` fixture of test_channels_api."""
import uuid

import pytest

from tests.test_agency_prices import as_member, noah
from tests.test_channels_api import agency, world  # noqa: F401  (a fixture)

pytestmark = pytest.mark.usefixtures("quiet_world")


async def two_agencies(db_conn):
    a_owner, b_owner = uuid.uuid4(), uuid.uuid4()
    a, a_slug = await agency(db_conn, "alpha-homes", (a_owner, "owner"))
    b, b_slug = await agency(db_conn, "beta-realty", (b_owner, "owner"))
    return {"a": a, "a_slug": a_slug, "a_owner": a_owner, "b": b, "b_slug": b_slug, "b_owner": b_owner}


async def test_the_card_names_the_agency_the_visitor_chose(world, db_conn):  # noqa: F811
    t = await two_agencies(db_conn)
    await db_conn.execute("update org_settings set brand_color = '#aa3300', logo_url = 'https://cdn.example.com/a.png' where org_id = %s", (t["a"],))
    card = (await world.client.get("/api/public/agency", headers={"X-Agency": t["a_slug"]})).json()
    assert card == {"slug": t["a_slug"], "name": "Alpha-Homes", "brand_color": "#aa3300", "logo_url": "https://cdn.example.com/a.png"}
    other = (await world.client.get("/api/public/agency", headers={"X-Agency": t["b_slug"]})).json()
    assert other["name"] == "Beta-Realty" and other["brand_color"] is None and other["slug"] == t["b_slug"]
    original = (await world.client.get("/api/public/agency")).json()
    assert original["slug"] == "proppulse", "no header: the original agency"


async def test_an_unknown_suspended_or_malformed_agency_is_not_found(world, db_conn):  # noqa: F811
    t = await two_agencies(db_conn)
    await db_conn.execute("update organizations set status = 'suspended' where id = %s", (t["b"],))
    for slug in ("no-such-agency", t["b_slug"], "UPPER_case!", "../etc", "a"):
        r = await world.client.get("/api/public/agency", headers={"X-Agency": slug})
        assert r.status_code == 404, slug
        assert (await world.client.get("/api/public/featured", headers={"X-Agency": slug})).status_code == 404, slug


async def test_the_directory_lists_agencies_that_opted_in_and_are_active_only(world, db_conn):  # noqa: F811
    t = await two_agencies(db_conn)
    c, c_slug = await agency(db_conn, "gamma-listed")
    d, d_slug = await agency(db_conn, "delta-suspended")
    await db_conn.execute("update org_settings set listed = true where org_id = any(%s)", ([t["a"], c, d],))
    await db_conn.execute("update organizations set status = 'suspended' where id = %s", (d,))
    listed = (await world.client.get("/api/public/agencies")).json()["agencies"]
    slugs = [x["slug"] for x in listed]
    assert t["a_slug"] in slugs and c_slug in slugs
    assert t["b_slug"] not in slugs, "not opted in"
    assert d_slug not in slugs, "suspended"
    assert set(listed[0]) == {"slug", "name", "brand_color", "logo_url"}, "nothing else about an agency is public"


async def test_an_agency_chooses_its_listing_and_look_and_bad_values_are_refused(world, db_conn):  # noqa: F811
    t = await two_agencies(db_conn)
    h = as_member(t["a_owner"], t["a"])
    patch = lambda body: world.client.patch("/api/org/settings", json=body, headers=h)  # noqa: E731
    ok = await patch({"listed": True, "brand_color": "#112233", "logo_url": "https://cdn.example.com/logo.png"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["settings"]["listed"] is True and ok.json()["settings"]["brand_color"] == "#112233"
    assert t["a_slug"] in [x["slug"] for x in (await world.client.get("/api/public/agencies")).json()["agencies"]]
    for bad in ({"brand_color": "red"}, {"brand_color": "#12345"}, {"logo_url": "http://insecure.example.com/x.png"}, {"logo_url": "javascript:alert(1)"},
                {"logo_url": 'https://x.example.com/"onerror=alert(1)'}):
        assert (await patch(bad)).status_code == 422, bad
    cleared = await patch({"brand_color": "", "logo_url": "", "listed": False})
    assert cleared.json()["settings"] == {**cleared.json()["settings"], "listed": False, "brand_color": None, "logo_url": None}
    viewer = uuid.uuid4()
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'viewer')", (t["a"], str(viewer)))
    assert (await world.client.patch("/api/org/settings", json={"listed": True}, headers=as_member(viewer, t["a"]))).status_code == 403


async def test_the_same_unit_shows_each_agencys_own_price_on_the_public_site(world, db_conn):  # noqa: F811
    t = await two_agencies(db_conn)
    pid, shared = await noah(db_conn)
    r = await world.client.put(f"/api/agency-prices/property/{pid}", json={"price_etb": shared - 1_234_000}, headers=as_member(t["a_owner"], t["a"]))
    assert r.status_code == 200, r.text
    price = lambda slug: world.client.get(f"/api/public/featured?hero={pid}", headers={"X-Agency": slug})  # noqa: E731
    mine, theirs = (await price(t["a_slug"])).json(), (await price(t["b_slug"])).json()
    assert float(mine["hero"]["price_etb"]) == shared - 1_234_000
    assert float(theirs["hero"]["price_etb"]) == shared, "another agency still shows the shared price"
