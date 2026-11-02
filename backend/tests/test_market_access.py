"""The market data is shared by every agency, so only the platform and the operator's own agency may change it."""
import uuid

import pytest

from app.access import NOT_OPERATOR
from app.tenancy import DEFAULT_ORG_ID
from tests.test_access_roles import ADMIN_KEY, api, bearer, org_with  # noqa: F401

PROJECT = uuid.uuid4()

# Every route that writes the shared market tables, with a body that passes validation (the handler may still answer 404/422).
MARKET_ROUTES = [
    ("post", "/api/properties/00000000-0000-4000-8000-0000000000aa/price", {"price_etb": 1_000_000}),
    ("post", "/api/ingest", {}),
    ("post", "/api/ingest/portal", {}),
    ("post", "/api/ingest/portal/sync", {}),
    ("post", "/api/portal/runs", {}),
    ("post", "/api/portal/runs/report", {}),
    ("put", "/api/developers/nobody/scrape", {}),
    ("post", "/api/developers/nobody/suggest-profile", {}),
    ("post", "/api/developers/nobody/crawl-projects", {}),
    ("post", "/api/developers", {"name": "Zed Homes", "sales_email": "sales@zed.example"}),
    ("put", f"/api/projects/{PROJECT}/price", {}),
    ("put", f"/api/projects/{PROJECT}/promo", {}),
    ("post", f"/api/projects/{PROJECT}/media", {}),
    ("delete", f"/api/projects/{PROJECT}/media/1", None),
    ("put", f"/api/projects/{PROJECT}/image", {}),
    ("post", "/api/cron/scrape-market", None),
]


async def call(client, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return await client.request(method.upper(), path, **kwargs)


async def passed_the_guard(client, method, path, body, headers) -> bool:
    """True when the request got as far as the handler. This test app has no ingestion or crawl services, so a handler that needs one
    fails with AttributeError: that is past the guard, which is all these tests ask."""
    try:
        r = await call(client, method, path, body, headers)
    except AttributeError:
        return True
    return not (r.status_code == 403 and r.json().get("detail") == NOT_OPERATOR)


@pytest.mark.parametrize(("method", "path", "body"), MARKET_ROUTES)
async def test_an_ordinary_agencys_owner_cannot_change_the_market(api, db_conn, method, path, body):  # noqa: F811
    owner = uuid.uuid4()
    await org_with(db_conn, "ordinary", [(owner, "owner")])
    r = await call(api, method, path, body, bearer(owner))
    assert r.status_code == 403 and r.json()["detail"] == NOT_OPERATOR


@pytest.mark.parametrize(("method", "path", "body"), MARKET_ROUTES)
async def test_the_platform_key_and_the_operators_own_agency_pass_the_guard(api, db_conn, method, path, body):  # noqa: F811
    admin = uuid.uuid4()
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (DEFAULT_ORG_ID, str(admin)))
    for headers in (ADMIN_KEY, bearer(admin)):
        assert await passed_the_guard(api, method, path, body, headers), headers


async def test_a_member_below_admin_of_the_operators_agency_still_needs_the_role(api, db_conn):  # noqa: F811
    viewer = uuid.uuid4()
    await db_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'viewer')", (DEFAULT_ORG_ID, str(viewer)))
    r = await call(api, "put", "/api/developers/nobody/scrape", {}, bearer(viewer))
    assert r.status_code == 403 and r.json()["detail"] != NOT_OPERATOR, "refused for the role, not for the agency"


async def test_an_agency_still_reads_the_market_it_cannot_edit(api, db_conn):  # noqa: F811
    owner = uuid.uuid4()
    await org_with(db_conn, "reader", [(owner, "owner")])
    assert (await api.get("/api/projects", headers=bearer(owner))).status_code == 200
