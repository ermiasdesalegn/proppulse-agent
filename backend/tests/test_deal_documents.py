"""Deal documents (migration 037): private storage, PDF and images only, 10 MB, members of the agency only, every upload and delete audited.

The store here is a local folder (and, for the Supabase store, a fake HTTP transport): what only a real private bucket can confirm is
listed in docs/PLAN_PRODUCT_AND_MONEY.md.
"""
import httpx
import pytest

from app.deal_documents import MAX_BYTES, LocalDiskStore, StorageError, SupabaseStore, clean_filename, sniff
from app.repository import LedgerEntry
from tests.jobs_helpers import app_of
from tests.test_agency_prices import as_member, buyer_of, noah
from tests.test_channels_api import agency, world  # noqa: F401
from tests.test_developer_terms import two  # noqa: F401

pytestmark = pytest.mark.usefixtures("quiet_world")

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


@pytest.fixture
def store(world, tmp_path):
    s = LocalDiskStore(tmp_path)
    app_of(world.client).state.deal_documents = s
    return s


async def deal(world, org, name, pid):
    buyer = await buyer_of(world, org, name)
    async with world.runtime.scope(org):
        (row,) = await world.repo.create_ledger_rows(buyer_id=buyer["id"], tour_id=None, entries=[LedgerEntry(pid)])
    return row


async def upload(world, row, headers, data=PDF, kind="receipt", name="receipt.pdf", ctype="application/pdf"):
    return await world.client.post(f"/api/ledger/{row['id']}/documents", params={"kind": kind, "filename": name}, content=data,
                                   headers={**headers, "content-type": ctype})


async def test_upload_list_download_delete_and_the_audit_trail(world, db_conn, two, store, tmp_path):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    r = await upload(world, row, h)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "receipt" and doc["filename"] == "receipt.pdf" and doc["size_bytes"] == len(PDF) and doc["content_type"] == "application/pdf"
    stored = list(tmp_path.rglob("*.pdf"))
    assert len(stored) == 1 and stored[0].relative_to(tmp_path).parts[:2] == (str(two["a"]), str(row["id"])), "kept under the agency and deal"
    listed = (await world.client.get(f"/api/ledger/{row['id']}/documents", headers=as_member(two["viewer_a"], two["a"]))).json()["documents"]
    assert [d["id"] for d in listed] == [doc["id"]]
    got = await world.client.get(f"/api/ledger/{row['id']}/documents/{doc['id']}", headers=as_member(two["viewer_a"], two["a"]))
    assert got.status_code == 200 and got.content == PDF and got.headers["content-type"] == "application/pdf"
    assert "attachment" in got.headers["content-disposition"] and got.headers["x-content-type-options"] == "nosniff"
    gone = await world.client.delete(f"/api/ledger/{row['id']}/documents/{doc['id']}", headers=h)
    assert gone.status_code == 200 and list(tmp_path.rglob("*.pdf")) == []
    assert (await world.client.get(f"/api/ledger/{row['id']}/documents/{doc['id']}", headers=h)).status_code == 404
    audited = [r["action"] for r in await (await db_conn.execute("select action from audit_log where org_id = %s order by id", (two["a"],))).fetchall()]
    assert audited == ["document.upload", "document.delete"]


async def test_another_agency_and_a_viewer_get_nothing(world, db_conn, two, store):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    doc = (await upload(world, row, as_member(two["owner_a"], two["a"]))).json()
    other = as_member(two["owner_b"], two["b"])
    url = f"/api/ledger/{row['id']}/documents"
    assert (await world.client.get(url, headers=other)).status_code == 404
    assert (await world.client.get(f"{url}/{doc['id']}", headers=other)).status_code == 404
    assert (await world.client.delete(f"{url}/{doc['id']}", headers=other)).status_code == 404
    assert (await upload(world, row, other)).status_code == 404
    assert (await upload(world, row, as_member(two["viewer_a"], two["a"]))).status_code == 403
    assert (await world.client.delete(f"{url}/{doc['id']}", headers=as_member(two["viewer_a"], two["a"]))).status_code == 403
    assert (await world.client.get(url)).status_code in (401, 503), "no sign-in, no list"


async def test_only_pdf_and_images_judged_by_their_bytes(world, db_conn, two, store, tmp_path):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    assert (await upload(world, row, h, data=b"<html><script>alert(1)</script></html>", name="x.pdf", ctype="application/pdf")).status_code == 415
    assert (await upload(world, row, h, data=b"MZ\x90\x00", name="x.png", ctype="image/png")).status_code == 415
    assert (await upload(world, row, h, data=b"", name="x.pdf")).status_code == 422
    assert (await upload(world, row, h, kind="selfie")).status_code == 422
    ok = await upload(world, row, h, data=PNG, name="../../etc/passwd", ctype="text/plain")
    assert ok.status_code == 201 and ok.json()["content_type"] == "image/png" and ok.json()["filename"] == "passwd"
    assert [p.suffix for p in tmp_path.rglob("*") if p.is_file()] == [".png"], "the extension comes from the bytes, not the name"


async def test_the_size_limit_is_10_mb(world, db_conn, two, store):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    assert (await upload(world, row, h, data=PDF + b"0" * (MAX_BYTES - len(PDF) + 1))).status_code == 413
    assert (await upload(world, row, h, data=PDF + b"0" * (MAX_BYTES - len(PDF)))).status_code == 201


async def test_a_deal_holds_at_most_20_documents(world, db_conn, two, store):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    for _ in range(20):
        assert (await upload(world, row, h)).status_code == 201
    assert (await upload(world, row, h)).status_code == 409


async def test_without_storage_it_says_so(world, db_conn, two):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    r = await upload(world, row, as_member(two["owner_a"], two["a"]))
    assert r.status_code == 503 and "not set up" in r.json()["detail"]


async def test_a_failing_store_attaches_nothing_and_keeps_the_file_on_a_failed_delete(world, db_conn, two, store):
    pid, _ = await noah(db_conn)
    row = await deal(world, two["a"], "abel", pid)
    h = as_member(two["owner_a"], two["a"])
    doc = (await upload(world, row, h)).json()

    async def boom(*a, **k):
        raise StorageError("down")

    store.delete = boom
    assert (await world.client.delete(f"/api/ledger/{row['id']}/documents/{doc['id']}", headers=h)).status_code == 502
    assert len((await world.client.get(f"/api/ledger/{row['id']}/documents", headers=h)).json()["documents"]) == 1
    store.put = boom
    assert (await upload(world, row, h)).status_code == 502
    assert len((await world.client.get(f"/api/ledger/{row['id']}/documents", headers=h)).json()["documents"]) == 1


def test_helpers():
    assert sniff(PDF) == "application/pdf" and sniff(PNG) == "image/png" and sniff(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp" and sniff(b"GIF89a") is None
    assert clean_filename('..\\..\\a"b;c.pdf') == "abc.pdf" and clean_filename("") == "document" and len(clean_filename("x" * 500)) == 120
    assert clean_filename("line\r\nbreak.pdf") == "linebreak.pdf"


async def test_the_disk_store_refuses_a_key_that_escapes_its_folder(tmp_path):
    s = LocalDiskStore(tmp_path / "docs")
    with pytest.raises(StorageError):
        await s.put("../outside.pdf", b"x", "application/pdf")


async def test_the_supabase_store_speaks_to_private_endpoints_with_the_service_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization"), request.headers.get("content-type")))
        return httpx.Response(200, content=PDF) if request.method == "GET" else httpx.Response(404 if request.method == "DELETE" else 200, json={})

    s = SupabaseStore("https://p.supabase.co/", "svc-key", "deal-docs", transport=httpx.MockTransport(handler))
    await s.put("org/deal/doc.pdf", PDF, "application/pdf")
    assert await s.get("org/deal/doc.pdf") == PDF
    await s.delete("org/deal/doc.pdf")  # a missing object is already deleted
    assert [m for m, *_ in seen] == ["POST", "GET", "DELETE"]
    assert all(path == "/storage/v1/object/deal-docs/org/deal/doc.pdf" and "/public/" not in path and auth == "Bearer svc-key" for _, path, auth, _ in seen)
    assert seen[0][3] == "application/pdf"
    failing = SupabaseStore("https://p.supabase.co", "k", "b", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(StorageError):
        await failing.put("k", b"x", "application/pdf")
