"""Where a deal's documents are kept, and what is allowed in.

Two stores behind one small interface: a private Supabase Storage bucket (production; the file is only ever fetched by this server with the
service key and handed to a signed-in member, never linked by URL) and a local folder (development and tests only). Storage is off until one
of the two is configured, because a local folder on a host that restarts with an empty disk would lose people's contracts silently.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_DEAL = 20
KINDS = ("contract", "receipt", "acknowledgement", "other")
EXTENSIONS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class StorageError(Exception):
    pass


def sniff(data: bytes) -> str | None:
    """The real type of a file from its first bytes. The browser's label and the file name are not trusted."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_filename(name: str | None) -> str:
    """A name safe to show and to put in a download header: no folders, control characters or quotes, at most 120 characters."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r'[\x00-\x1f\x7f"<>|?*;]', "", base).strip(" .")
    return (base or "document")[:120]


def storage_key(org_id, ledger_id, doc_id, content_type: str) -> str:
    return f"{org_id}/{ledger_id}/{doc_id}.{EXTENSIONS[content_type]}"


class DocumentStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalDiskStore:
    """A folder on this machine. For development and tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("bad key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise StorageError("the file is missing from storage") from None

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class SupabaseStore:
    """A private Supabase Storage bucket, reached with the service key from this server only. Uses the authenticated object
    endpoints, never the public ones, so a leaked key name or path opens nothing."""

    def __init__(self, url: str, service_key: str, bucket: str, transport: httpx.AsyncBaseTransport | None = None):
        self._base = f"{url.rstrip('/')}/storage/v1/object/{quote(bucket, safe='')}"
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self._transport = transport

    def _url(self, key: str) -> str:
        return f"{self._base}/{quote(key, safe='/')}"

    async def _call(self, method: str, key: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(transport=self._transport, timeout=30) as client:
            return await client.request(method, self._url(key), headers={**self._headers, **kw.pop("headers", {})}, **kw)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        res = await self._call("POST", key, content=data, headers={"Content-Type": content_type, "x-upsert": "false"})
        if res.status_code >= 300:
            raise StorageError(f"storage refused the upload ({res.status_code})")

    async def get(self, key: str) -> bytes:
        res = await self._call("GET", key)
        if res.status_code >= 300:
            raise StorageError(f"storage could not give the file ({res.status_code})")
        return res.content

    async def delete(self, key: str) -> None:
        res = await self._call("DELETE", key)
        if res.status_code >= 300 and res.status_code != 404:
            raise StorageError(f"storage could not delete the file ({res.status_code})")


def build_store(settings) -> DocumentStore | None:
    if settings.deal_documents_bucket and settings.supabase_url and settings.supabase_service_role_key:
        return SupabaseStore(settings.supabase_url, settings.supabase_service_role_key, settings.deal_documents_bucket)
    if settings.deal_documents_dir:
        return LocalDiskStore(settings.deal_documents_dir)
    return None
