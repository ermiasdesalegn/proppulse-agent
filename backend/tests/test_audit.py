"""The audit trail: changes to people and channels are recorded with who did them, secrets never are, and agencies cannot alter it."""
import json
import uuid

import psycopg
import pytest

from tests.test_channels_api import TOKEN_A, agency, bearer, world  # noqa: F401  (the fixture is used by name)
from tests.test_tenancy_isolation import as_org


async def entries(world, user):  # noqa: F811
    r = await world.client.get("/api/audit", headers=bearer(user))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


async def test_connecting_a_bot_is_recorded_with_who_did_it_and_without_the_token(world, db_conn):  # noqa: F811
    admin = uuid.uuid4()
    await agency(db_conn, "audit-a", (admin, "admin"))
    assert (await world.client.put("/api/channels/telegram", json={"bot_token": TOKEN_A}, headers=bearer(admin))).status_code == 200
    (entry,) = await entries(world, admin)
    assert entry["action"] == "channel.telegram_connect" and entry["actor_kind"] == "member" and entry["actor_id"] == str(admin)
    assert entry["target"] == "bot_111"
    assert TOKEN_A not in json.dumps(entry)


async def test_role_changes_and_invitations_are_recorded(world, db_conn):  # noqa: F811
    owner, agent = uuid.uuid4(), uuid.uuid4()
    await agency(db_conn, "audit-b", (owner, "owner"), (agent, "agent"))
    h = bearer(owner)
    assert (await world.client.patch(f"/api/members/{agent}", json={"role": "admin"}, headers=h)).status_code == 200
    invite = await world.client.post("/api/invitations", json={"email": "new@example.com", "role": "viewer"}, headers=h)
    assert invite.status_code == 201
    assert (await world.client.delete(f"/api/invitations/{invite.json()['id']}", headers=h)).status_code == 200
    got = await entries(world, owner)
    assert [e["action"] for e in got] == ["invitation.revoke", "invitation.create", "member.role_change"]  # newest first
    change = got[2]
    assert change["target"] == str(agent) and change["detail"] == {"old_role": "agent", "new_role": "admin"}
    assert got[1]["target"] == "new@example.com" and "token" not in json.dumps(got[1])


async def test_only_admins_read_the_trail_and_only_their_own_agencys(world, db_conn):  # noqa: F811
    admin_a, viewer_a, admin_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await agency(db_conn, "audit-c", (admin_a, "admin"), (viewer_a, "viewer"))
    await agency(db_conn, "audit-d", (admin_b, "admin"))
    await world.client.patch("/api/org/settings", json={"broker_name": "A"}, headers=bearer(admin_a))
    await world.client.patch("/api/org/settings", json={"broker_name": "B"}, headers=bearer(admin_b))
    assert (await world.client.get("/api/audit", headers=bearer(viewer_a))).status_code == 403
    a, b = await entries(world, admin_a), await entries(world, admin_b)
    assert [e["action"] for e in a] == ["settings.change"] and [e["action"] for e in b] == ["settings.change"]
    assert a[0]["actor_id"] == str(admin_a) and b[0]["actor_id"] == str(admin_b)


async def test_an_agency_cannot_add_change_or_remove_entries(world, db_conn):  # noqa: F811
    admin = uuid.uuid4()
    org, _ = await agency(db_conn, "audit-e", (admin, "admin"))
    await world.client.patch("/api/org/settings", json={"broker_name": "A"}, headers=bearer(admin))
    async with as_org(db_conn, org):
        assert (await (await db_conn.execute("select count(*) as n from audit_log")).fetchone())["n"] == 1
        for statement in ("insert into audit_log (org_id, actor_kind, action) values (current_org(), 'member', 'forged')",
                          "update audit_log set action = 'settings.nothing'", "delete from audit_log"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with db_conn.transaction():
                    await db_conn.execute(statement)


async def test_not_even_the_owner_role_can_rewrite_history(db_conn):
    org = (await (await db_conn.execute("insert into organizations (slug, name) values ('audit-f', 'F') returning id")).fetchone())["id"]
    await db_conn.execute("insert into audit_log (org_id, actor_kind, action) values (%s, 'system', 'org.create')", (org,))
    with pytest.raises(psycopg.errors.RaiseException):
        async with db_conn.transaction():
            await db_conn.execute("update audit_log set action = 'org.create' where org_id = %s", (org,))
