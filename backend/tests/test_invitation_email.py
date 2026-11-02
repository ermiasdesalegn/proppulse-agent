"""Invitations are sent by email (app/org_api.py): the link works once, for the invited address only; a resend replaces the old link; a
revoked invitation sends nothing; the link token is never logged and is returned only when no email could be sent."""
import json
import logging
import re
import uuid

import httpx
import pytest

from app.runtime import _active
from app.services.email import EmailError, ResendEmailService, display_name
from tests.jobs_helpers import app_of
from tests.test_channels_api import agency, bearer, world  # noqa: F401  (a fixture)


@pytest.fixture
async def team(world, db_conn):  # noqa: F811
    owner = uuid.uuid4()
    org, _ = await agency(db_conn, "invite-mail", (owner, "owner"))
    await db_conn.execute("update org_settings set display_name = 'Bole Homes <Ltd>' where org_id = %s", (org,))
    # The developer's .env may hold a real Resend key: the agency's own services are built from the runtime's settings, so blank it there or
    # the tests would call the real provider. (The server's own settings below say email is "configured" so the invitation is sent.)
    world.runtime.settings = world.runtime.settings.model_copy(update={"resend_api_key": "", "email_backend": "mock"})
    state = app_of(world.client).state
    state.settings = state.settings.model_copy(update={"resend_api_key": "re_test_key", "frontend_origin": "https://console.example.com,http://localhost:3000"})
    return {"org": org, "owner": owner, "headers": {**bearer(owner), "X-Org-Id": str(org)}}


async def mails(world, org):
    async with world.runtime.scope(org):
        return list(world.runtime.proxied_services().email.sent)


def token_in(mail):
    (token,) = re.findall(r"/join\?token=([A-Za-z0-9_-]+)", mail["html"])
    return token


async def test_an_invitation_is_emailed_with_a_link_that_accepts_it_once_for_that_address_only(world, team, db_conn):  # noqa: F811
    r = await world.client.post("/api/invitations", json={"email": "Guest@Example.com", "role": "viewer"}, headers=team["headers"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emailed"] is True and "token" not in body and "token" not in r.text, "the token is only in the email"

    (mail,) = await mails(world, team["org"])
    assert mail["to"] == "guest@example.com" and "Bole Homes" in mail["subject"] and mail["from_name"] == "Bole Homes <Ltd>"
    assert "<Ltd>" not in mail["html"] and "&lt;Ltd&gt;" in mail["html"], "the agency's name is escaped in the body"
    assert "viewer" in mail["html"] and "only works for that address" in mail["html"] and mail["html"].count("https://console.example.com/join?token=") == 1
    token = token_in(mail)
    stored = (await (await db_conn.execute("select token_hash from invitations where org_id = %s", (team["org"],))).fetchone())["token_hash"]
    assert token not in stored

    guest = uuid.uuid4()
    wrong = await world.client.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="other@example.com"))
    assert wrong.status_code == 403
    joined = await world.client.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="guest@example.com"))
    assert joined.status_code == 200 and joined.json()["role"] == "viewer"
    again = await world.client.post("/api/invitations/accept", json={"token": token}, headers=bearer(guest, email="guest@example.com"))
    assert again.status_code == 404, "a link works once"


async def test_a_resend_replaces_the_old_link_and_a_revoked_invitation_sends_nothing(world, team):  # noqa: F811
    first = (await world.client.post("/api/invitations", json={"email": "a@example.com", "role": "agent"}, headers=team["headers"])).json()
    old = token_in((await mails(world, team["org"]))[0])
    r = await world.client.post(f"/api/invitations/{first['id']}/resend", headers=team["headers"])
    assert r.status_code == 200 and r.json()["emailed"] is True and "token" not in r.text
    sent = await mails(world, team["org"])
    new = token_in(sent[1])
    assert len(sent) == 2 and new != old
    guest = uuid.uuid4()
    assert (await world.client.post("/api/invitations/accept", json={"token": old}, headers=bearer(guest, email="a@example.com"))).status_code == 404
    assert (await world.client.post("/api/invitations/accept", json={"token": new}, headers=bearer(guest, email="a@example.com"))).status_code == 200

    second = (await world.client.post("/api/invitations", json={"email": "b@example.com", "role": "agent"}, headers=team["headers"])).json()
    assert (await world.client.delete(f"/api/invitations/{second['id']}", headers=team["headers"])).status_code == 200
    count = len(await mails(world, team["org"]))
    assert (await world.client.post(f"/api/invitations/{second['id']}/resend", headers=team["headers"])).status_code == 404
    assert (await world.client.post(f"/api/invitations/{first['id']}/resend", headers=team["headers"])).status_code == 404, "an accepted one is not open any more"
    assert len(await mails(world, team["org"])) == count, "nothing was sent for a revoked or accepted invitation"


async def test_only_an_admin_may_resend_and_it_is_recorded(world, team, db_conn):  # noqa: F811
    agent = uuid.uuid4()
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'agent')", (team["org"], str(agent)))
    made = (await world.client.post("/api/invitations", json={"email": "c@example.com", "role": "viewer"}, headers=team["headers"])).json()
    assert (await world.client.post(f"/api/invitations/{made['id']}/resend", headers={**bearer(agent), "X-Org-Id": str(team["org"])})).status_code == 403
    assert (await world.client.post(f"/api/invitations/{made['id']}/resend")).status_code == 401
    await world.client.post(f"/api/invitations/{made['id']}/resend", headers=team["headers"])
    actions = [r["action"] for r in await (await db_conn.execute("select action from audit_log where org_id = %s order by seq", (team["org"],))).fetchall()]
    assert actions == ["invitation.create", "invitation.resend"]


async def test_without_email_the_link_is_returned_for_copying_by_hand(world, team):  # noqa: F811
    state = app_of(world.client).state
    state.settings = state.settings.model_copy(update={"resend_api_key": ""})
    r = await world.client.post("/api/invitations", json={"email": "d@example.com", "role": "agent"}, headers=team["headers"])
    assert r.status_code == 201 and r.json()["emailed"] is False and len(r.json()["token"]) >= 20 and "copy the link" in r.json()["note"]
    assert await mails(world, team["org"]) == []


async def test_a_failing_mail_provider_keeps_the_invitation_and_never_logs_the_token(world, team, caplog, monkeypatch):  # noqa: F811
    async with world.runtime.scope(team["org"]):
        target = _active.get().services.email  # the agency's own email service, cached for a minute

    async def down(**kw):
        raise EmailError(f"Resend returned 500: could not deliver {kw['html']}")  # an error that echoes the message, link and all

    monkeypatch.setattr(target, "send", down)
    with caplog.at_level(logging.DEBUG):
        r = await world.client.post("/api/invitations", json={"email": "e@example.com", "role": "agent"}, headers=team["headers"])
    assert r.status_code == 201 and r.json()["emailed"] is False and "could not be sent" in r.json()["note"]
    token = r.json()["token"]
    assert token not in caplog.text and "invitation email could not be sent (EmailError)" in caplog.text
    assert (await world.client.get("/api/invitations", headers=team["headers"])).json()["invitations"][0]["email"] == "e@example.com"


async def test_the_sender_shows_the_agency_and_cannot_be_used_to_inject_a_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "m1"})

    svc = ResendEmailService("key", "PropPulse <noreply@proppulse.example>", transport=httpx.MockTransport(handler))
    await svc.send(to="x@example.com", subject="s", html="h", from_name='Bole "Homes" <evil@x.com>\r\nBcc: a@b.c')
    assert "\n" not in seen["from"] and "\r" not in seen["from"] and seen["from"].count("<") == 1
    assert seen["from"].endswith(" via PropPulse <noreply@proppulse.example>") and seen["from"].startswith("Bole Homes")
    await svc.send(to="x@example.com", subject="s", html="h")
    assert seen["from"] == "PropPulse <noreply@proppulse.example>"
    assert display_name("<>") == ""
