"""Who is calling, for which organization, and what may they do there?

A signed-in person (a Supabase token, as for buyers) names the organization they act for in `X-Org-Id`; the database says whether
they belong to it and in which role. The platform's own secret (`X-Admin-Key`, kept for the cron jobs and for operators) acts as an
owner of the organization named in the same header, or of the original one when none is named. Either way the request then runs
inside `use_tenant(...)`, so every repository call is confined to that organization by row-level security.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, HTTPException, Request

from app.buyer_auth import BuyerIdentity, TokenError, verifier_of
from app.tenancy import DEFAULT_ORG_ID, TenantContext, use_tenant

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Principal:
    tenant: TenantContext
    identity: BuyerIdentity | None  # None when the platform secret was used
    platform: bool = False


def _same(a: str | None, b: str) -> bool:
    import hmac

    return bool(a) and hmac.compare_digest((a or "").encode(), b.encode())


def _bearer(authorization: str | None) -> str | None:
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _parse_org(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        raise HTTPException(400, "X-Org-Id must be an organization id") from None


def default_role(method: str) -> str:
    """Looking is for everyone in the organization; changing things needs an agent."""
    return "viewer" if method.upper() in READ_METHODS else "agent"


async def identify(request: Request, authorization: str | None) -> BuyerIdentity:
    token = _bearer(authorization)
    if token is None:
        raise HTTPException(401, "Please sign in")
    try:
        return await verifier_of(request).verify_async(token)
    except TokenError as exc:
        raise HTTPException(401, str(exc)) from None


async def _platform_principal(request: Request, x_admin_key: str, org_id: UUID | None) -> Principal:
    key = request.app.state.settings.admin_api_key
    if not key:
        raise HTTPException(503, "ADMIN_API_KEY is not configured")
    if not _same(x_admin_key, key):
        raise HTTPException(401, "Invalid admin key")
    if org_id is not None and await request.app.state.repo.as_platform().organization(org_id) is None:
        raise HTTPException(404, "No such organization")
    return Principal(TenantContext(org_id or DEFAULT_ORG_ID, role="owner"), identity=None, platform=True)


async def _member_principal(request: Request, authorization: str | None, org_id: UUID | None, need: str) -> Principal:
    who = await identify(request, authorization)
    mine = await request.app.state.repo.as_platform().memberships_for_user(who.user_id)
    if not mine:
        raise HTTPException(403, "You do not belong to any organization")
    if org_id is None:
        if len(mine) > 1:
            raise HTTPException(400, "You belong to several organizations; say which one with X-Org-Id")
        chosen = mine[0]
    else:
        chosen = next((m for m in mine if m["org_id"] == org_id), None)
        if chosen is None:  # the same answer whether the organization exists or not
            raise HTTPException(403, "You do not belong to that organization")
    if chosen["status"] != "active":
        raise HTTPException(403, "This organization is suspended")
    tenant = TenantContext(chosen["org_id"], user_id=UUID(who.user_id), role=chosen["role"])
    if not tenant.at_least(need):
        raise HTTPException(403, f"This needs the {need} role or higher; you are {chosen['role']}")
    return Principal(tenant, identity=who)


def require_role(minimum: str | None = None):
    """A route guard. `minimum` is the weakest role allowed; None picks one from the HTTP method (read: viewer, write: agent).

    The route then runs as the caller's organization. The handler can ask for `Depends(principal_of)` to learn who called.
    """

    async def guard(
        request: Request,
        authorization: str | None = Header(default=None),
        x_admin_key: str | None = Header(default=None),
        x_org_id: str | None = Header(default=None),
    ) -> AsyncIterator[Principal]:
        need = minimum or default_role(request.method)
        org_id = _parse_org(x_org_id)
        if x_admin_key is not None or _bearer(authorization) is None:
            principal = await _platform_principal(request, x_admin_key or "", org_id)
        else:
            principal = await _member_principal(request, authorization, org_id, need)
        request.state.principal = principal
        with use_tenant(principal.tenant):
            yield principal

    return guard


def principal_of(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Please sign in")
    return principal


# The guards used by the console routes.
console_access = require_role()          # read: viewer, write: agent
console_admin = require_role("admin")    # settings, members, shared-data jobs
console_owner = require_role("owner")    # the most destructive operations

