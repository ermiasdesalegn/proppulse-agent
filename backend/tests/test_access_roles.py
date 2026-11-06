"""Who may call the console API: members by role, the platform secret, and the organization endpoints. Real database, rolled back."""
import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio

from app.main import create_app
from app.tenancy import current_tenant
from tests.agent_helpers import FakeGateway, ext, make_chat

SECRET = "test-jwt-secret-that-is-long-enough-for-hs256"
OVERRIDES = dict(admin_api_key="test-admin-key", telegram_webhook_secret="hook", supabase_jwt_secret=SECRET, supabase_url="")
ADMIN_KEY = {"X-Admin-Key": "test-admin-key"}


def bearer(user, email="member@example.com"):
    claims = {"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 600, "email": email}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest_asyncio.fixture
async def api(spy_repo):
    chat, *_ = make_chat(spy_repo, FakeGateway([ext("small_talk") for _ in range(5)]), **OVERRIDES)
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat = chat.settings, spy_repo, chat
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def org_with(db_conn, name, members=(), status="active"):
    """An organization with one buyer and the given (user, role) members, created as the platform would."""
    cur = await db_conn.execute(
        "insert into organizations (slug, name, status) values (%s, %s, %s) returning id", (f"t-{name}-{uuid.uuid4().hex[:6]}", name, status))
    org = (await cur.fetchone())["id"]
    cur = await db_conn.execute("insert into buyers_crm (org_id, thread_id) values (%s, %s) returning id", (org, f"tg:{uuid.uuid4().hex[:8]}"))
    buyer = (await cur.fetchone())["id"]
    for user, role in members:
        await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, %s)", (org, str(user), role))
    return org, buyer


def ids(response):
    body = response.json()
    rows = body if isinstance(body, list) else body.get("buyers", body.get("items", []))
    return {str(r["id"]) for r in rows}


# ---- reading and writing by role ----

async def test_a_member_sees_only_their_own_organization(api, db_conn):
    u = uuid.uuid4()
    org_a, buyer_a = await org_with(db_conn, "a", [(u, "viewer")])
    _, buyer_b = await org_with(db_conn, "b")
    r = await api.get("/api/buyers", headers=bearer(u))
    assert r.status_code == 200
    found = ids(r)
    assert str(buyer_a) in found and str(buyer_b) not in found


async def test_roles_decide_what_a_member_may_change(api, db_conn):
    viewer, agent, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _, buyer = await org_with(db_conn, "team", [(viewer, "viewer"), (agent, "agent"), (admin, "admin")])
    takeover = f"/api/buyers/{buyer}/takeover"
    assert (await api.post(takeover, headers=bearer(viewer))).status_code == 403, "a viewer only looks"
    assert (await api.post(takeover, headers=bearer(agent))).status_code != 403, "an agent may act"
    dev = "/api/developers/nobody/scrape"  # shared-data setup: never for an ordinary agency (see test_market_access.py)
    assert (await api.put(dev, json={}, headers=bearer(agent))).status_code == 403
    assert (await api.put(dev, json={}, headers=bearer(admin))).status_code == 403
    reset = "/api/demo/reset"
    assert (await api.post(reset, json={"confirm": "RESET"}, headers=bearer(admin))).status_code == 403, "reset is for owners"


async def test_signed_in_but_in_no_organization_is_forbidden(api, db_conn):
    assert (await api.get("/api/buyers", headers=bearer(uuid.uuid4()))).status_code == 403
    assert (await api.get("/api/buyers")).status_code == 401, "not signed in at all"
    assert (await api.get("/api/buyers", headers={"Authorization": "Bearer nonsense"})).status_code == 401


async def test_someone_in_several_organizations_must_choose_and_cannot_choose_a_stranger(api, db_conn):
    u = uuid.uuid4()
    org_a, buyer_a = await org_with(db_conn, "one", [(u, "agent")])
    org_b, buyer_b = await org_with(db_conn, "two", [(u, "viewer")])
    stranger, _ = await org_with(db_conn, "stranger")
    assert (await api.get("/api/buyers", headers=bearer(u))).status_code == 400
    r = await api.get("/api/buyers", headers={**bearer(u), "X-Org-Id": str(org_b)})
    assert r.status_code == 200 and str(buyer_b) in ids(r) and str(buyer_a) not in ids(r)
    assert (await api.get("/api/buyers", headers={**bearer(u), "X-Org-Id": str(stranger)})).status_code == 403
    assert (await api.get("/api/buyers", headers={**bearer(u), "X-Org-Id": str(uuid.uuid4())})).status_code == 403, "same answer for an unknown one"
    assert (await api.get("/api/buyers", headers={**bearer(u), "X-Org-Id": "nope"})).status_code == 400


async def test_a_suspended_organization_is_closed_to_its_members(api, db_conn):
    u = uuid.uuid4()
    await org_with(db_conn, "paused", [(u, "owner")], status="suspended")
    assert (await api.get("/api/buyers", headers=bearer(u))).status_code == 403


# ---- the platform secret ----

async def test_the_platform_key_acts_for_the_named_organization_or_the_original_one(api, db_conn):
    org, buyer = await org_with(db_conn, "named")
    r = await api.get("/api/buyers", headers={**ADMIN_KEY, "X-Org-Id": str(org)})
    assert r.status_code == 200 and ids(r) == {str(buyer)}
    assert (await api.get("/api/buyers", headers={**ADMIN_KEY, "X-Org-Id": str(uuid.uuid4())})).status_code == 404
    assert (await api.get("/api/buyers", headers=ADMIN_KEY)).status_code == 200, "no header: the original organization"
    assert (await api.get("/api/buyers", headers={"X-Admin-Key": "wrong"})).status_code == 401


async def test_no_organization_is_left_in_scope_after_a_request(api, db_conn):
    u = uuid.uuid4()
    await org_with(db_conn, "scoped", [(u, "viewer")])
    await api.get("/api/buyers", headers=bearer(u))
    assert current_tenant() is None


# ---- creating an organization and running its team ----

async def test_anyone_signed_in_can_create_an_agency_and_becomes_its_owner(api, db_conn):
    u = uuid.uuid4()
    r = await api.post("/api/orgs", json={"name": "Sheger Homes"}, headers=bearer(u))
    assert r.status_code == 201 and r.json()["slug"] == "sheger-homes" and r.json()["role"] == "owner"
    mine = (await api.get("/api/orgs/mine", headers=bearer(u))).json()["organizations"]
    assert [(o["slug"], o["role"]) for o in mine] == [("sheger-homes", "owner")]
    assert (await api.get("/api/buyers", headers=bearer(u))).status_code == 200, "the new owner can use the console at once"
    assert (await api.post("/api/orgs", json={"name": "Again", "slug": "sheger-homes"}, headers=bearer(uuid.uuid4()))).status_code == 409
    assert (await api.post("/api/orgs", json={"name": "Admin", "slug": "admin"}, headers=bearer(uuid.uuid4()))).status_code == 409
    assert (await api.post("/api/orgs", json={"name": "No sign-in"})).status_code == 401


async def test_one_person_cannot_own_an_unbounded_number_of_organizations(api, db_conn):
    u = uuid.uuid4()
    for i in range(5):
        assert (await api.post("/api/orgs", json={"name": f"Agency {i}"}, headers=bearer(u))).status_code == 201
    assert (await api.post("/api/orgs", json={"name": "One too many"}, headers=bearer(u))).status_code == 409


async def test_member_management_follows_the_role_ladder(api, db_conn):
    owner, admin, agent, newcomer = (uuid.uuid4() for _ in range(4))
    org, _ = await org_with(db_conn, "ladder", [(owner, "owner"), (admin, "admin"), (agent, "agent")])
    h = lambda u: {**bearer(u), "X-Org-Id": str(org)}  # noqa: E731
    assert len((await api.get("/api/members", headers=h(agent))).json()["members"]) == 3
    assert (await api.patch(f"/api/members/{agent}", json={"role": "viewer"}, headers=h(agent))).status_code == 403
    assert (await api.patch(f"/api/members/{agent}", json={"role": "viewer"}, headers=h(admin))).status_code == 200
    assert (await api.patch(f"/api/members/{agent}", json={"role": "owner"}, headers=h(admin))).status_code == 403, "an admin cannot make an owner"
    assert (await api.patch(f"/api/members/{owner}", json={"role": "viewer"}, headers=h(admin))).status_code == 403, "nor touch one"
    assert (await api.patch(f"/api/members/{agent}", json={"role": "owner"}, headers=h(owner))).status_code == 200
    assert (await api.patch(f"/api/members/{newcomer}", json={"role": "agent"}, headers=h(owner))).status_code == 404


async def test_an_organization_always_keeps_an_owner(api, db_conn):
    owner, second = uuid.uuid4(), uuid.uuid4()
    org, _ = await org_with(db_conn, "keepowner", [(owner, "owner")])
    h = {**bearer(owner), "X-Org-Id": str(org)}
    assert (await api.patch(f"/api/members/{owner}", json={"role": "admin"}, headers=h)).status_code == 409
    assert (await api.delete(f"/api/members/{owner}", headers=h)).status_code == 409
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (org, str(second)))
    assert (await api.delete(f"/api/members/{owner}", headers=h)).status_code == 200, "with a second owner, the first may leave"


async def test_anyone_may_leave_but_only_admins_remove_others(api, db_conn):
    owner, admin, agent, other = (uuid.uuid4() for _ in range(4))
    org, _ = await org_with(db_conn, "leave", [(owner, "owner"), (admin, "admin"), (agent, "agent"), (other, "agent")])
    h = lambda u: {**bearer(u), "X-Org-Id": str(org)}  # noqa: E731
    assert (await api.delete(f"/api/members/{other}", headers=h(agent))).status_code == 403
    assert (await api.delete(f"/api/members/{owner}", headers=h(admin))).status_code == 403
    assert (await api.delete(f"/api/members/{other}", headers=h(admin))).status_code == 200
    assert (await api.delete(f"/api/members/{agent}", headers=h(agent))).status_code == 200, "leaving is always allowed"


async def test_invitation_flow(api, db_conn):
    owner, agent, guest = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    org, _ = await org_with(db_conn, "invite", [(owner, "owner"), (agent, "agent")])
    h = lambda u, **kw: {**bearer(u, **kw), "X-Org-Id": str(org)}  # noqa: E731
    assert (await api.post("/api/invitations", json={"email": "new@example.com", "role": "agent"}, headers=h(agent))).status_code == 403
    assert (await api.post("/api/invitations", json={"email": "not-an-email", "role": "agent"}, headers=h(owner))).status_code == 422
    assert (await api.post("/api/invitations", json={"email": "x@example.com", "role": "owner"}, headers=h(owner))).status_code == 422

    made = await api.post("/api/invitations", json={"email": "New@Example.com", "role": "viewer"}, headers=h(owner))
    assert made.status_code == 201
    token = made.json()["token"]
    assert (await api.post("/api/invitations", json={"email": "new@example.com", "role": "agent"}, headers=h(owner))).status_code == 409
    resp = await api.get("/api/invitations", headers=h(owner))
    assert resp.status_code == 200, resp.text
    listed = resp.json()["invitations"]
    assert [i["email"] for i in listed] == ["new@example.com"] and "token" not in listed[0]
    stored = (await (await db_conn.execute("select token_hash from invitations")).fetchone())["token_hash"]
    assert stored != token and token not in stored, "only a hash of the link token is kept"

    wrong = await api.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="someone-else@example.com"))
    assert wrong.status_code == 403
    assert (await api.post("/api/invitations/accept", json={"token": "x" * 40}, headers=bearer(guest, email="new@example.com"))).status_code == 404
    joined = await api.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="new@example.com"))
    assert joined.status_code == 200 and joined.json()["role"] == "viewer" and joined.json()["id"] == str(org)
    assert (await api.get("/api/buyers", headers=bearer(guest))).status_code == 200
    assert (await api.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="new@example.com"))).status_code == 404, "a link works once"


async def test_a_revoked_or_expired_invitation_no_longer_works(api, db_conn):
    owner, guest = uuid.uuid4(), uuid.uuid4()
    org, _ = await org_with(db_conn, "revoke", [(owner, "owner")])
    h = {**bearer(owner), "X-Org-Id": str(org)}
    first = (await api.post("/api/invitations", json={"email": "a@example.com", "role": "agent"}, headers=h)).json()
    assert (await api.delete(f"/api/invitations/{first['id']}", headers=h)).status_code == 200
    assert (await api.post("/api/invitations/accept", json={"token": first["token"]}, headers=bearer(guest, email="a@example.com"))).status_code == 404
    second = (await api.post("/api/invitations", json={"email": "b@example.com", "role": "agent"}, headers=h)).json()
    await db_conn.execute("update invitations set expires_at = now() - interval '1 minute' where id = %s", (second["id"],))
    assert (await api.post("/api/invitations/accept", json={"token": second["token"]}, headers=bearer(guest, email="b@example.com"))).status_code == 404


async def test_an_invitation_of_one_organization_is_invisible_to_another(api, db_conn):
    o1, o2 = uuid.uuid4(), uuid.uuid4()
    org1, _ = await org_with(db_conn, "inv1", [(o1, "owner")])
    org2, _ = await org_with(db_conn, "inv2", [(o2, "owner")])
    made = (await api.post("/api/invitations", json={"email": "z@example.com", "role": "agent"}, headers={**bearer(o1), "X-Org-Id": str(org1)})).json()
    assert (await api.get("/api/invitations", headers={**bearer(o2), "X-Org-Id": str(org2)})).json()["invitations"] == []
    assert (await api.delete(f"/api/invitations/{made['id']}", headers={**bearer(o2), "X-Org-Id": str(org2)})).status_code == 404


@pytest.mark.parametrize("method,path", [("get", "/api/members"), ("get", "/api/invitations"), ("post", "/api/invitations"), ("get", "/api/orgs/mine")])
async def test_the_team_endpoints_need_a_sign_in(api, method, path):
    assert (await getattr(api, method)(path)).status_code in (401, 422)


def test_the_grant_script_validates_before_touching_anything():
    from scripts.grant_role import grant

    for kwargs in ({}, {"email": "a@b.co", "user_id": str(uuid.uuid4())}):
        with pytest.raises(SystemExit):
            grant("postgresql://unused", "x", "owner", **kwargs)
    with pytest.raises(SystemExit):
        grant("postgresql://unused", "x", "superuser", user_id=str(uuid.uuid4()))


async def test_the_grant_script_makes_an_owner_and_demotion_keeps_one(db_conn):
    """The script commits on its own connection, so it works on a throwaway organization that is removed afterwards."""
    import psycopg

    from app.config import get_settings
    from scripts.grant_role import grant

    url = get_settings().database_url
    slug = f"grant-{uuid.uuid4().hex[:8]}"
    user = str(uuid.uuid4())
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute("insert into organizations (slug, name) values (%s, 'Grant test')", (slug,))
        try:
            assert grant(url, slug, "owner", user_id=user) == f"{user} is now owner of {slug}"
            row = admin.execute("select role from memberships where user_id = %s", (user,)).fetchone()
            assert row[0] == "owner"
            with pytest.raises(psycopg.errors.RaiseException):
                grant(url, slug, "viewer", user_id=user)  # the only owner cannot be demoted
            with pytest.raises(SystemExit):
                grant(url, "no-such-org", "owner", user_id=user)
        finally:
            admin.execute("delete from organizations where slug = %s", (slug,))
