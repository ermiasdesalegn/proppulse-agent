"""The audit trail: a line for every change to who may do what in an agency, and to how it is connected.

Only changes are recorded (never reads), and never a secret: the detail holds names, roles and addresses, not tokens or keys.
Writing never stops the action it describes from succeeding; a failure is logged and the request carries on.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.access import Principal

log = logging.getLogger(__name__)

ACTIONS = frozenset({
    "org.create", "member.role_change", "member.remove", "member.leave", "invitation.create", "invitation.revoke", "invitation.accept",
    "channel.telegram_connect", "channel.telegram_disconnect", "channel.calendar_connect", "channel.calendar_disconnect", "settings.change",
    "job.retry", "price.override_set", "price.override_clear", "key.rewrap", "key.rotate", "invitation.resend", "terms.set", "terms.clear", "org.plan_change", "org.status_change",
    "claim.send", "claim.acknowledge", "claim.dispute", "deal.assign", "deal.split", "document.upload", "document.delete", "billing.upgrade", "org.rename",
})


async def record(request: Request, p: Principal | None, action: str, target: str | None = None, *, org_id: Any = None,
                 actor_id: Any = None, **detail: Any) -> None:
    """Add one entry for the organization in `p` (or `org_id`, when nobody is in scope yet, as when someone accepts an invitation)."""
    assert action in ACTIONS, f"unknown audit action {action!r}"
    try:
        org = org_id if org_id is not None else p.tenant.org_id  # type: ignore[union-attr]
        if p is None:
            kind, who = "member", actor_id
        elif p.platform:
            kind, who = "platform", None
        else:
            kind, who = "member", p.tenant.user_id
        await request.app.state.repo.as_platform().add_audit(org, kind, who, action, target, {k: v for k, v in detail.items() if v is not None})
    except Exception:
        log.exception("audit entry %s could not be written", action)
