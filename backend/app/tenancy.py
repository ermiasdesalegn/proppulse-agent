"""Which organization a piece of work belongs to, and how that reaches the database.

A request (or a webhook, or a job) runs `with use_tenant(...)`. Every repository call then opens its connection through
`tenant_connection`, which records the organization in the transaction with `set_config('app.org_id', ..., true)`. The `true`
makes the setting local to that transaction: it vanishes at commit or rollback, so a pooled connection never carries one
organization's id into the next caller, even behind Supabase's transaction pooler. Row-level security (a later step) reads it.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.pq import TransactionStatus

# The organization that existed before multi-tenancy. While `tenancy_strict` is off, work with no organization falls back to it.
DEFAULT_ORG_ID = UUID("00000000-0000-4000-8000-000000000001")

ROLES = ("viewer", "agent", "admin", "owner")  # weakest to strongest


class MissingTenant(RuntimeError):
    """Database work was attempted with no organization in scope while strict tenancy is on."""


@dataclass(frozen=True)
class TenantContext:
    org_id: UUID
    user_id: UUID | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if self.role is not None and self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")

    def at_least(self, role: str) -> bool:
        """True when this member's role is `role` or stronger. No role (a webhook, a job) never passes."""
        return self.role is not None and ROLES.index(self.role) >= ROLES.index(role)


_current: ContextVar[TenantContext | None] = ContextVar("current_tenant", default=None)


def current_tenant() -> TenantContext | None:
    return _current.get()


@contextmanager
def use_tenant(org: TenantContext | UUID | str) -> Iterator[TenantContext]:
    """Run the enclosed work (including tasks it starts) as one organization."""
    ctx = org if isinstance(org, TenantContext) else TenantContext(org_id=UUID(str(org)))
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def resolve_org_id(pinned: UUID | str | None = None, *, strict: bool = False) -> UUID:
    """The organization for a database call: an explicitly pinned one, else the one in scope, else the legacy default."""
    if pinned is not None:
        return UUID(str(pinned))
    if ctx := _current.get():
        return ctx.org_id
    if strict:
        raise MissingTenant("no organization in scope; wrap the work in use_tenant(...)")
    return DEFAULT_ORG_ID


APP_ROLE = "app_user"  # owns nothing and cannot bypass row-level security; see migration 022


async def _enter(conn: Any, org_id: UUID | str, role: str | None) -> None:
    await conn.execute("select set_config('app.org_id', %s, true)", (str(org_id),))
    if role:
        await conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))


@asynccontextmanager
async def tenant_connection(pool: Any, org_id: UUID | str, role: str | None = APP_ROLE) -> AsyncIterator[Any]:
    """A pooled connection whose current transaction is labelled with `org_id`.

    Unless `role` is None the transaction also runs as that role, which is what makes row-level security apply.
    The label lives exactly as long as the transaction it was set in. On a normal pooled connection that is the implicit
    transaction the pool commits (or rolls back on error) when the block ends, so this adds no commit of its own and a caller
    that manages its own transaction (the tests roll theirs back) stays in control. A connection in autocommit mode has no
    implicit transaction, so one is opened there to keep the label from being dropped after a single statement.
    """
    async with pool.connection() as conn:
        if getattr(conn, "autocommit", False):
            async with conn.transaction():
                await _enter(conn, org_id, role)
                yield conn
        else:
            await _enter(conn, org_id, role)
            try:
                yield conn
            finally:
                # Back to the connection's own role, so a caller that keeps the transaction open (or a failure that was handled
                # inside it) does not leave the connection restricted for whoever uses it next. A transaction that is already
                # aborted gets rolled back by the pool, which drops the role with it, and cannot run a statement anyway.
                if role and conn.info.transaction_status != TransactionStatus.INERROR:
                    await conn.execute("reset role")
