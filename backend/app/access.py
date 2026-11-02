"""Who is calling, for which organization, and what may they do there?

A signed-in person (a Supabase token, as for buyers) names the organization they act for in `X-Org-Id`; the database says whether
they belong to it and in which role. The platform's own secret (`X-Admin-Key`, kept for the cron jobs and for operators) acts as an
owner of the organization named in the same header, or of the original one when none is named. Either way the request then runs
inside `use_tenant(...)`, so every repository call is confined to that organization by row-level security.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from app.buyer_auth import BuyerIdentity, TokenError, verifier_of
from app.tenancy import DEFAULT_ORG_ID, TenantContext, use_tenant

log = logging.getLogger(__name__)

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
        async with in_scope(request, principal.tenant):
            yield principal

    return guard


@asynccontextmanager
async def in_scope(request: Request, org: TenantContext | UUID) -> AsyncIterator[TenantContext]:
    """Run as one organization: its rows, and (when the server has a runtime) its settings and channels too."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        try:
            async with runtime.scope(org) as tenant:
                yield tenant
        except LookupError:
            raise HTTPException(404, "No such organization") from None
    else:
        with use_tenant(org) as tenant:
            yield tenant


AGENCY_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


async def agency_scope(request: Request, x_agency: str | None = Header(default=None)) -> AsyncIterator[None]:
    """For the public website: which agency a visitor is talking to. No header means the original agency; an unknown or
    suspended one is "not found", the same answer either way."""
    if not x_agency:
        yield
        return
    slug = x_agency.strip().lower()
    org = await request.app.state.repo.as_platform().org_by_slug(slug) if AGENCY_SLUG.match(slug) else None
    if org is None or org["status"] != "active":
        raise HTTPException(404, "No such agency")
    async with in_scope(request, org["id"]):
        yield


def job_access(minimum: str = "agent", *, operator_only: bool = False):
    """Guard for the scheduled jobs. Returns the organizations the job should run for.

    The cron secret runs a job for every active organization (or for the one named in X-Org-Id). The operator's admin key runs it
    for the one named, or the original one. A signed-in member of at least `minimum` role runs it for their own organization only,
    which is what the console's buttons do. With `operator_only` a member must belong to the operator's own agency.
    """

    async def guard(
        request: Request,
        x_cron_secret: str | None = Header(default=None),
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_org_id: str | None = Header(default=None),
    ) -> list[UUID]:
        s = request.app.state.settings
        org = _parse_org(x_org_id)
        platform = request.app.state.repo.as_platform()

        async def named() -> list[UUID]:
            if await platform.organization(org) is None:  # type: ignore[arg-type]
                raise HTTPException(404, "No such organization")
            return [org]  # type: ignore[list-item]

        if s.cron_secret and _same(x_cron_secret, s.cron_secret):
            return await named() if org else await platform.active_org_ids()
        if x_cron_secret is None and x_admin_key is None and _bearer(authorization):
            member = await _member_principal(request, authorization, org, minimum)
            if operator_only and member.tenant.org_id != OPERATOR_ORG_ID:
                raise HTTPException(403, NOT_OPERATOR)
            return [member.tenant.org_id]
        if not s.cron_secret and not s.admin_api_key:
            raise HTTPException(503, "Neither CRON_SECRET nor ADMIN_API_KEY is configured")
        if s.admin_api_key and _same(x_admin_key, s.admin_api_key):
            return await named() if org else [DEFAULT_ORG_ID]
        raise HTTPException(401, "Invalid credentials")

    return guard


async def run_for_orgs(request: Request, orgs: list[UUID], job) -> dict:
    """Run one job once for each organization, each inside its own scope, and combine the answers.

    One agency's failure (a revoked bot, a broken calendar key) is reported and never stops the others. With a single
    organization the job's own answer is returned unchanged.
    """
    repo = request.app.state.repo.as_platform()
    results: list[tuple[str, dict]] = []
    for org in orgs:
        slug = str((await repo.organization(org) or {}).get("slug") or org)
        try:
            async with in_scope(request, org):
                results.append((slug, await job()))
        except Exception as exc:
            log.exception("job failed for organization %s", slug)
            results.append((slug, {"sent": 0, "skipped": 0, "failed": 1, "error": type(exc).__name__}))
    if len(results) == 1:
        return results[0][1]
    combined: dict[str, Any] = {"organizations": [slug for slug, _ in results], "per_organization": dict(results)}
    for key in ("sent", "skipped", "failed"):
        combined[key] = sum(int(r.get(key, 0)) for _, r in results)
    combined["dry_run"] = any(r.get("dry_run") for _, r in results)
    combined["jobs"] = [{**j, "organization": slug} for slug, r in results for j in (r.get("jobs") or [r])]
    return combined


def principal_of(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Please sign in")
    return principal


# The guards used by the console routes.
console_access = require_role()          # read: viewer, write: agent
console_admin = require_role("admin")    # settings, members, shared-data jobs
console_owner = require_role("owner")    # the most destructive operations


# The market data (listings, projects, developers, crawls) is shared by every agency, so changing it changes it for all of them.
# Only the platform (the admin key) and the members of the operator's own agency, the original organization, may do that.
OPERATOR_ORG_ID = DEFAULT_ORG_ID
NOT_OPERATOR = "Shared market data can only be changed by the platform operator"


def market_editor(minimum: str):
    guard = require_role(minimum)

    async def check(p: Principal = Depends(guard)) -> Principal:
        if not (p.platform or p.tenant.org_id == OPERATOR_ORG_ID):
            raise HTTPException(403, NOT_OPERATOR)
        return p

    return check


market_admin = market_editor("admin")   # crawls, ingestion, developers, price changes
market_agent = market_editor("agent")   # a project's price, offer, pictures

