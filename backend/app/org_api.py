"""Organizations, their members and invitations: the endpoints a signed-in person uses to create an agency and run its team."""
from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.access import OPERATOR_ORG_ID, Principal, console_access, console_admin, identify, require_role
from app.audit import record
from app.repository import Repository

router = APIRouter()
log = logging.getLogger(__name__)

MAX_OWNED_ORGANIZATIONS = 5
RESERVED_SLUGS = frozenset({"www", "api", "app", "admin", "console", "portal", "login", "signup", "auth", "static", "platform", "support", "join", "start", "agencies", "public", "robots", "sitemap", "health", "_next"})
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
Role = Literal["owner", "admin", "agent", "viewer"]


def make_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:39].strip("-")
    return slug if len(slug) >= 3 else f"{slug or 'agency'}-{secrets.token_hex(2)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def platform_repo(request: Request) -> Repository:
    """Tenancy tables are written outside any one organization's rights; the handlers below check the caller first."""
    return request.app.state.repo.as_platform()


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class RoleIn(BaseModel):
    role: Role


class InviteIn(BaseModel):
    email: str = Field(max_length=254)
    role: Literal["admin", "agent", "viewer"] = "agent"  # an owner is made by promoting a member, never by link


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


def _org_view(m: dict) -> dict:
    # `operator`: this is the platform operator's own agency, the only one that may edit the shared market data (app/access.py).
    return {"id": str(m["org_id"]), "slug": m["slug"], "name": m["name"], "role": m["role"], "plan": m.get("plan"), "status": m.get("status"),
            "operator": m["org_id"] == OPERATOR_ORG_ID}


async def _mine(request: Request) -> list[dict]:
    who = await identify(request, request.headers.get("authorization"))
    return [_org_view(m) for m in await platform_repo(request).memberships_for_user(who.user_id)]


@router.get("/api/orgs/mine")
async def organizations_mine(request: Request):
    """The organizations the signed-in person belongs to, with their role in each. Needs a sign-in, not an organization."""
    return {"organizations": await _mine(request)}


@router.post("/api/orgs", status_code=201)
async def create_organization(body: OrgIn, request: Request):
    who = await identify(request, request.headers.get("authorization"))
    repo = platform_repo(request)
    if await repo.count_owned_organizations(who.user_id) >= MAX_OWNED_ORGANIZATIONS:
        raise HTTPException(409, f"You can own at most {MAX_OWNED_ORGANIZATIONS} organizations")
    slug = body.slug or make_slug(body.name)
    if slug in RESERVED_SLUGS:
        raise HTTPException(409, "That address is reserved")
    try:
        org = await repo.create_organization(body.name.strip(), slug, who.user_id)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "That address is already taken") from None
    await record(request, None, "org.create", slug, org_id=org["id"], actor_id=who.user_id, name=org["name"])
    return {"id": str(org["id"]), "slug": org["slug"], "name": org["name"], "role": "owner", "plan": org["plan"], "status": org["status"]}


@router.get("/api/members")
async def list_members(request: Request, p: Principal = Depends(console_access)):
    members = await platform_repo(request).list_members(p.tenant.org_id)
    return {"members": [{"user_id": str(m["user_id"]), "role": m["role"], "since": m["created_at"]} for m in members]}


def _may_change(p: Principal, target_role: str, new_role: str | None = None) -> None:
    """Only an owner touches an owner (or makes one); an admin manages everyone below."""
    if (target_role == "owner" or new_role == "owner") and not p.tenant.at_least("owner"):
        raise HTTPException(403, "Only an owner can change an owner or make one")


@router.patch("/api/members/{user_id}")
async def change_member_role(user_id: UUID, body: RoleIn, request: Request, p: Principal = Depends(console_admin)):
    repo = platform_repo(request)
    member = await repo.get_member(p.tenant.org_id, user_id)
    if member is None:
        raise HTTPException(404, "No such member")
    _may_change(p, member["role"], body.role)
    try:
        row = await repo.set_member_role(p.tenant.org_id, user_id, body.role)
    except psycopg.errors.RaiseException:
        raise HTTPException(409, "An organization must keep at least one owner") from None
    await record(request, p, "member.role_change", str(user_id), old_role=member["role"], new_role=body.role)
    return {"user_id": str(user_id), "role": row["role"] if row else body.role}


@router.delete("/api/members/{user_id}")
async def remove_member(user_id: UUID, request: Request, p: Principal = Depends(require_role("viewer"))):
    """Anyone may leave; removing someone else takes an admin (an owner, for an owner)."""
    repo = platform_repo(request)
    member = await repo.get_member(p.tenant.org_id, user_id)
    if member is None:
        raise HTTPException(404, "No such member")
    if p.tenant.user_id != user_id:
        if not p.tenant.at_least("admin"):
            raise HTTPException(403, "Only an admin can remove other members")
        _may_change(p, member["role"])
    try:
        await repo.remove_member(p.tenant.org_id, user_id)
    except psycopg.errors.RaiseException:
        raise HTTPException(409, "An organization must keep at least one owner") from None
    await record(request, p, "member.leave" if p.tenant.user_id == user_id else "member.remove", str(user_id), role=member["role"])
    return {"removed": str(user_id)}


@router.post("/api/invitations", status_code=201)
async def invite(body: InviteIn, request: Request, p: Principal = Depends(console_admin)):
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "That does not look like an email address")
    token = secrets.token_urlsafe(32)
    try:
        row = await request.app.state.repo.create_invitation(email, body.role, hash_token(token), p.tenant.user_id)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "There is already an open invitation for that address") from None
    await record(request, p, "invitation.create", email, role=body.role)
    return {"id": str(row["id"]), "email": row["email"], "role": row["role"], "expires_at": row["expires_at"],
            **await _deliver(request, p, row, token)}


async def _deliver(request: Request, p: Principal, row: dict, token: str) -> dict:
    """Email the invitation link. Only its hash is kept, so the link exists in the email and, when no email could be sent (not configured, or
    the provider failed), in this answer so an admin can pass it on by hand. Once the email is sent the token is not returned."""
    s = request.app.state.settings
    if not s.resend_api_key:
        return {"emailed": False, "token": token, "note": "Email is not set up on this server; copy the link to the person yourself."}
    profile = await platform_repo(request).org_profile(p.tenant.org_id)
    agency = (profile or {}).get("display_name") or (profile or {}).get("name") or "Your agency"
    origin = s.frontend_origin.split(",")[0].strip().rstrip("/")
    link = f"{origin}/join?token={token}"
    expires = row["expires_at"].strftime("%d %B %Y")
    body = (
        f"<p>{html.escape(agency)} has invited you to join its team on PropPulse as <b>{html.escape(row['role'])}</b>.</p>"
        f'<p><a href="{html.escape(link)}">Accept the invitation</a></p>'
        f"<p>Sign in (or sign up) with <b>{html.escape(row['email'])}</b>: the link only works for that address, once, and until {expires}.</p>"
        "<p>If you were not expecting this, ignore this email; nothing happens until the link is used.</p>")
    try:
        await request.app.state.proactive.services.email.send(
            to=row["email"], subject=f"{agency} invited you to join its team", html=body, from_name=agency,
            idempotency_key=f"invite-{row['id']}-{hash_token(token)[:16]}")
    except Exception as exc:  # the invitation exists; the admin can still pass the link on. The error text could echo the link, so only its type is logged
        log.warning("invitation email could not be sent (%s)", type(exc).__name__)
        return {"emailed": False, "token": token, "note": "The email could not be sent; copy the link to the person yourself."}
    return {"emailed": True}


@router.get("/api/invitations")
async def list_invitations(request: Request, p: Principal = Depends(console_admin)):
    rows = await request.app.state.repo.open_invitations()
    return {"invitations": [{**r, "id": str(r["id"])} for r in rows]}


@router.post("/api/invitations/{invitation_id}/resend")
async def resend_invitation(invitation_id: UUID, request: Request, p: Principal = Depends(console_admin)):
    """A new link by email, and a fresh week. The old link stops working, so a link that went to the wrong place can be cancelled this way."""
    token = secrets.token_urlsafe(32)
    row = await request.app.state.repo.reissue_invitation(invitation_id, hash_token(token))
    if row is None:
        raise HTTPException(404, "No such open invitation")
    await record(request, p, "invitation.resend", row["email"], role=row["role"])
    return {"id": str(row["id"]), "email": row["email"], "role": row["role"], "expires_at": row["expires_at"], **await _deliver(request, p, row, token)}


@router.delete("/api/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: UUID, request: Request, p: Principal = Depends(console_admin)):
    if not await request.app.state.repo.revoke_invitation(invitation_id):
        raise HTTPException(404, "No such open invitation")
    await record(request, p, "invitation.revoke", str(invitation_id))
    return {"revoked": str(invitation_id)}


@router.post("/api/invitations/accept")
async def accept_invitation(body: AcceptIn, request: Request):
    """Join the organization an invitation was written for. Needs a sign-in whose email is the invited address."""
    who = await identify(request, request.headers.get("authorization"))
    try:
        joined = await platform_repo(request).accept_invitation(hash_token(body.token), who.user_id, who.email)
    except PermissionError:
        raise HTTPException(403, "This invitation was sent to a different email address") from None
    if joined is None:
        raise HTTPException(404, "This invitation is not valid any more")
    await record(request, None, "invitation.accept", who.email, org_id=joined["org_id"], actor_id=who.user_id, role=joined.get("role"))
    return _org_view(joined)


@router.get("/api/audit")
async def audit_trail(request: Request, limit: int = 100, before: int | None = None, p: Principal = Depends(console_admin)):
    """The agency's change history, newest first. Admins and owners only."""
    rows = await request.app.state.repo.list_audit(max(1, min(limit, 500)), before)
    return {"entries": [{**r, "actor_id": None if r["actor_id"] is None else str(r["actor_id"])} for r in rows]}
