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
