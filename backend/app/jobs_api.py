"""The job queue, seen from the console: what is queued, running, failed or dead, and an operator's retry.

The `jobs` table is the platform's (an agency's role cannot read it), so these routes check who is asking and then read it through
the queue. An agency's admin sees its own agency's jobs; the operator (the platform key, or an admin of the operator's agency) sees
every agency's and is the only one who may retry a job.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app import audit
from app.access import OPERATOR_ORG_ID, Principal, market_editor, require_role
from app.api import jobs_of
from app.keys import ROTATE, key_status

router = APIRouter()

jobs_reader = require_role("admin")
operator_admin = market_editor("admin")  # the platform, or an admin of the operator's own agency

Status = Literal["queued", "running", "done", "failed", "dead"]


def _is_operator(p: Principal) -> bool:
    return p.platform or p.tenant.org_id == OPERATOR_ORG_ID


@router.get("/api/jobs")
async def list_jobs(request: Request, status: Status | None = None, org_id: UUID | None = None, limit: int = 50,
                    p: Principal = Depends(jobs_reader)):
    """Recent jobs, newest first, with a count per status. An agency sees its own; the operator sees all, or one agency's with `org_id`."""
    operator = _is_operator(p)
    scope = org_id if operator else p.tenant.org_id
    queue = jobs_of(request)
    return {
        "operator": operator,
        "counts": await queue.counts(org_id=scope),
        "jobs": await queue.list(org_id=scope, status=status, limit=limit),
    }


@router.get("/api/keys/status", dependencies=[Depends(operator_admin)])
async def master_key_status(request: Request):
    """Which master key seals how many agencies' data keys: ids and counts only, never a key."""
    return await key_status(request.app.state.repo.pool, request.app.state.runtime.vault)


async def _queue_rotation(request: Request, p: Principal, org_id: UUID) -> dict:
    if await request.app.state.repo.as_platform().org_key(org_id) is None:
        raise HTTPException(404, "This agency has no stored credentials, so it has no data key to replace")
    who = {"by": "platform" if p.platform else "member", "user_id": None if p.platform else str(p.tenant.user_id)}
    queued = await jobs_of(request).enqueue(org_id, ROTATE, who, max_attempts=1)  # a new job each time; a failed one is not retried by itself
    return {"job_id": queued.id, "org_id": str(org_id), "kind": queued.kind}


@router.post("/api/keys/agency/{org_id}/rotate", status_code=202)
async def rotate_agency_key(request: Request, org_id: UUID, p: Principal = Depends(operator_admin)):
    """The operator replaces one agency's data key (the agency asked, or its key may have leaked) and re-seals its secrets."""
    return await _queue_rotation(request, p, org_id)


@router.post("/api/org/keys/rotate", status_code=202)
async def rotate_own_key(request: Request, p: Principal = Depends(require_role("owner"))):
    """An agency's owner replaces its own data key. Only its own agency, whatever the request names."""
    return await _queue_rotation(request, p, p.tenant.org_id)


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: int, p: Principal = Depends(operator_admin)):
    queue = jobs_of(request)
    before = await queue.get(job_id)
    if before is None:
        raise HTTPException(404, "No such job")
    retried = await queue.retry(job_id)
    if retried is None:
        raise HTTPException(409, f"Only a dead or failed job can be retried; this one is {before['status']}")
    await audit.record(request, p, "job.retry", f"{before['kind']} #{job_id}", org_id=before["org_id"], status=before["status"])
    return {"id": retried["id"], "status": retried["status"], "kind": retried["kind"]}
