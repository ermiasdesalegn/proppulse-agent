"""Documents attached to a deal (migration 037): a contract, a receipt, the developer's acknowledgement. Members of the agency only."""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app import audit
from app.access import Principal, require_role
from app.deal_documents import KINDS, MAX_BYTES, MAX_PER_DEAL, StorageError, build_store, clean_filename, sniff, storage_key

log = logging.getLogger(__name__)
router = APIRouter()

reader = require_role("viewer")
writer = require_role("agent")


def store_of(request: Request):
    store = getattr(request.app.state, "deal_documents", None) or build_store(request.app.state.settings)
    if store is None:
        raise HTTPException(503, "Document storage is not set up on this server (set DEAL_DOCUMENTS_BUCKET with the Supabase settings), so nothing can be attached yet.")
    return store


def _shown(row: dict) -> dict:
    return {"id": str(row["id"]), "kind": row["kind"], "filename": row["filename"], "content_type": row["content_type"],
            "size_bytes": row["size_bytes"], "created_at": row["created_at"], "uploaded_by": str(row["uploaded_by"]) if row["uploaded_by"] else None}


async def _deal(request: Request, ledger_id: UUID) -> dict:
    row = await request.app.state.repo.ledger_for_claim(ledger_id)
    if row is None:
        raise HTTPException(404, "Deal not found")
    return row


@router.get("/api/ledger/{ledger_id}/documents")
async def list_documents(ledger_id: UUID, request: Request, p: Principal = Depends(reader)):
    await _deal(request, ledger_id)
    return {"documents": [_shown(r) for r in await request.app.state.repo.list_deal_documents(ledger_id)]}


@router.post("/api/ledger/{ledger_id}/documents", status_code=201)
async def upload_document(ledger_id: UUID, request: Request, kind: str = "other", filename: str = "", p: Principal = Depends(writer)):
    """The file is the request body. Its type is read from its bytes (PDF, PNG, JPEG or WebP), at most 10 MB, 20 per deal."""
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(413, "A document can be at most 10 MB")
    store = store_of(request)
    deal = await _deal(request, ledger_id)
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "A document can be at most 10 MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    content_type = sniff(data)
    if content_type is None:
        raise HTTPException(415, "Only PDF, PNG, JPEG and WebP files can be attached")
    repo = request.app.state.repo
    if len(await repo.list_deal_documents(ledger_id)) >= MAX_PER_DEAL:
        raise HTTPException(409, f"A deal can hold at most {MAX_PER_DEAL} documents")
    doc_id = uuid4()
    key = storage_key(p.tenant.org_id, ledger_id, doc_id, content_type)
    try:
        await store.put(key, data, content_type)
    except StorageError as exc:
        log.warning("deal document upload failed: %s", exc)
        raise HTTPException(502, "The file could not be stored, so nothing was attached. Try again shortly.") from None
    name = clean_filename(filename)
    try:
        row = await repo.add_deal_document(doc_id, ledger_id, kind=kind, filename=name, content_type=content_type, size_bytes=len(data),
                                           storage_key=key, uploaded_by=p.tenant.user_id)
    except Exception:
        try:
            await store.delete(key)  # no row, so no file left behind
        except StorageError:
            log.exception("an orphaned document could not be removed: %s", key)
        raise
    await audit.record(request, p, "document.upload", deal["developer_name"], deal=str(ledger_id), document=str(doc_id), kind=kind, filename=name, size_bytes=len(data))
    return _shown(row)


@router.get("/api/ledger/{ledger_id}/documents/{doc_id}")
async def download_document(ledger_id: UUID, doc_id: UUID, request: Request, p: Principal = Depends(reader)):
    row = await request.app.state.repo.get_deal_document(ledger_id, doc_id)
    if row is None:
        raise HTTPException(404, "Document not found")
    try:
        data = await store_of(request).get(row["storage_key"])
    except StorageError:
        raise HTTPException(502, "The file could not be read from storage") from None
    return Response(data, media_type=row["content_type"], headers={
        "Content-Disposition": f'attachment; filename="{row["filename"]}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/api/ledger/{ledger_id}/documents/{doc_id}")
async def delete_document(ledger_id: UUID, doc_id: UUID, request: Request, p: Principal = Depends(writer)):
    repo = request.app.state.repo
    row = await repo.get_deal_document(ledger_id, doc_id)
    if row is None:
        raise HTTPException(404, "Document not found")
    try:
        await store_of(request).delete(row["storage_key"])
    except StorageError:
        raise HTTPException(502, "The file could not be removed from storage, so it was kept. Try again shortly.") from None
    await repo.delete_deal_document(doc_id)
    await audit.record(request, p, "document.delete", row["filename"], deal=str(ledger_id), document=str(doc_id), kind=row["kind"])
    return {"deleted": str(doc_id)}
