"""Portal crawler support: local extraction from real saved pages, the strict price rule, and the batch endpoint's model budget."""
from pathlib import Path

import httpx
import pytest

from app.ingest.models import ExtractedUnit, ListingExtraction, PortalItem
from app.ingest.portal import SiteHints, extract_listing, is_challenge_page, listing_ref, strict_price, to_unit
from tests.test_api_ingest import ADMIN, build
from tests.test_ingest_service import make

PAGES = Path(__file__).parent / "fixtures" / "portals"
ADDISLIST_HINTS = SiteHints(css={"price": "ul.property-price-wrap li.item-price span.price", "sub_city": "address.item-address", "deal": "a.label-status"})
BETOCH_HINTS = SiteHints(css={"title": "div.text-black.font-bold.text-xl", "price": "div.text-blue-600 p.text-xs"})

EPC_PAGE = """<html><head><title>For Sale: 3 Bedroom Flat, Bole</title>
<meta property="og:title" content="For Sale: 3 Bedroom Flat, Bole, Addis Ababa - Br 21,000,000" />
<script type="application/ld+json">{"@type":"RealEstateListing","name":"3 Bedroom Flat","offers":{"@type":"Offer","price":"21000000.00","priceCurrency":"ETB"}}</script>
</head><body><ul><li>Bedrooms: 3</li><li>Size: 184 m²</li></ul></body></html>"""


def read(name, url, hints=None):
    return extract_listing((PAGES / f"{name}.html").read_text(encoding="utf-8", errors="replace"), url, hints)


@pytest.mark.parametrize("name,url,hints,price,sqm,beds,area,source", [
    ("guzohomes", "https://guzohomes.com/property/6a89aafd002ec2bb72eb", SiteHints(ref_pattern=r"/property/([a-z0-9]+)"), 5_000_000, 250, 1, "Kazanchis", "json-ld"),
    ("livingethio", "https://livingethio.com/site/property-details/2252", None, 45_000_000, 300, None, "Ayat", "embedded-json"),
    ("ethiobetoch", "https://ethiobetoch.com/property/x-1142", BETOCH_HINTS, 200_000_000, 1500, 4, "Ayat", "css"),
    ("addislist", "https://addislist.com/property/semi-finished-apartment-for-sale-summit-72-ap181/", ADDISLIST_HINTS, 14_500_000, 152, 3, None, "css"),
])
def test_real_portal_pages_are_read_without_a_model(name, url, hints, price, sqm, beds, area, source):
    listing = read(name, url, hints)
    assert listing.price_etb == price and listing.sqm == sqm and listing.bedrooms == beds
    assert listing.found_by["price_etb"] == source
    if area:
        assert listing.sub_city == area


def test_an_area_that_is_not_a_known_one_is_kept_as_the_site_writes_it():
    listing = read("addislist", "https://addislist.com/property/semi-finished-apartment-for-sale-summit-72-ap181/", ADDISLIST_HINTS)
    unit = to_unit(listing, "Addis List")
    assert not isinstance(unit, str) and unit.sub_city == "Summit 72" and unit.price_etb == 14_500_000


def test_json_ld_and_labels_give_a_complete_unit():
    listing = extract_listing(EPC_PAGE, "https://ethiopiapropertycentre.com/for-sale/flats-apartments/addis-ababa/bole/11213-3-bedroom-flat")
    unit = to_unit(listing, "Ethiopia Property Centre")
    assert not isinstance(unit, str)
    assert (unit.price_etb, unit.bedrooms, unit.sqm, unit.sub_city, unit.unit_type) == (21_000_000, 3, 184, "Bole", "3BR Flat")
    assert unit.project_name.endswith("(Ethiopia Property Centre #11213)")


@pytest.mark.parametrize("value,expected", [
    (21_000_000, 21_000_000), ("21000000.00", 21_000_000), ("ETB14,500,000", 14_500_000), ("200", None), (200, None), (45_000, None), ("", None), (None, None),
    (5e11, None),
])
def test_only_a_plausible_birr_sale_price_is_strict(value, expected):
    assert strict_price(value) == expected


def test_a_price_filter_dropdown_is_never_mistaken_for_the_price():
    html = '<html><body><select><option value="5000000">5,000,000</option></select><p>3 Bedrooms 120 m²</p></body></html>'
    assert extract_listing(html, "https://x.test/p/123-flat").price_etb is None


def test_rentals_and_land_are_skipped_with_a_reason():
    rent = extract_listing(EPC_PAGE, "https://x.test/for-rent/flats/bole/1234-flat")
    assert to_unit(rent, "X") == "a rental, not a home for sale"
    land = extract_listing("<html><title>Residential Land for sale in Bole</title></html>", "https://x.test/p/9999-land")
    land.property_type = "Land"
    assert to_unit(land, "X") == "not a home (land, office, shop or similar)"


def test_a_bot_challenge_page_is_recognised():
    assert is_challenge_page('<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>challenges.cloudflare.com</body></html>')
    assert not is_challenge_page(EPC_PAGE)


@pytest.mark.parametrize("url,ref", [
    ("https://ethiopiapropertycentre.com/for-sale/flats-apartments/addis-ababa/bole/11213-3-bedroom-flat", "11213"),
    ("https://livingethio.com/site/property-details/2252", "2252"),
    ("https://ethiobetoch.com/property/%E1%89%A4%E1%89%B5-1142", "1142"),
])
def test_the_portals_own_id_names_the_listing(url, ref):
    assert listing_ref(url) == ref


# ---------------- the backend side ----------------

def item(ref, price=21_000_000.0, text=None, **over):
    base = dict(url=f"https://epc.test/for-sale/{ref}", ref=ref, title=f"Flat {ref}", price_etb=price, sqm=120, bedrooms=2, sub_city="Bole", deal="sale", text=text)
    return PortalItem(**{**base, **over})


async def test_strictly_priced_listings_cost_no_model_call_and_a_rerun_changes_nothing(repo, db_conn):
    service, gw, _ = make(repo)
    first = await service.run_portal(portal="epc-test", name="EPC Test", items=[item("1"), item("2", price=9_000_000)])
    assert first["counts"]["created"] == 2 and [c[0] for c in gw.calls] == ["embed_documents"]
    again = await service.run_portal(portal="epc-test", name="EPC Test", items=[item("1"), item("2", price=8_500_000)])
    assert again["counts"]["created"] == 0 and again["counts"]["price_changed"] == 1 and again["counts"]["unchanged"] == 1
    cur = await db_conn.execute("select sales_email, website_url from developers where slug = 'epc-test'")
    row = await cur.fetchone()
    assert row["sales_email"].endswith(".invalid") and row["website_url"] is None, "a portal is never mailed and never swept by the daily LLM job"


async def test_the_model_reads_only_pages_without_a_price_and_only_up_to_the_budget(repo):
    from tests.agent_helpers import FakeGateway

    gw = FakeGateway()
    gw.listing_extraction = ListingExtraction(units=[ExtractedUnit(project_name="p", unit_type="2BR Apartment", sub_city="Bole", bedrooms=2, sqm=100, price_etb=9_500_000)])
    service, _, _ = make(repo, gateway=gw)
    items = [item("10"), *(item(str(20 + i), price=None, text="2 bedroom apartment 100 sqm, 9.5 million birr") for i in range(3))]
    report = await service.run_portal(portal="epc-test", name="EPC Test", items=items, llm_budget=2)
    assert report["model_reads"] == 2 and [c[0] for c in gw.calls].count("extraction") == 2
    assert report["counts"]["created"] == 3
    assert any("budget" in s["reason"] for s in report["skipped"])


async def test_a_page_the_crawler_could_not_price_without_text_is_skipped_not_guessed(repo):
    service, gw, _ = make(repo)
    report = await service.run_portal(portal="epc-test", name="EPC Test", items=[item("30", price=None)])
    assert report["counts"]["created"] == 0 and report["skipped"][0]["reason"] == "missing price" and gw.calls == []


async def test_a_dry_run_of_a_new_portal_writes_nothing(repo, db_conn):
    service, _, _ = make(repo)
    report = await service.run_portal(portal="brand-new", name="Brand New", items=[item("40")], dry_run=True)
    assert report["counts"]["created"] == 1
    cur = await db_conn.execute("select count(*) n from developers where slug = 'brand-new'")
    assert (await cur.fetchone())["n"] == 0


async def test_the_portal_endpoint_needs_the_admin_key_and_validates_items(spy_repo):
    client, _ = await build(spy_repo)
    body = {"portal": "epc-test", "name": "EPC Test", "items": [item("50").model_dump()]}
    async with client:
        assert (await client.post("/api/ingest/portal", json=body)).status_code == 401
        res = await client.post("/api/ingest/portal", json=body, headers=ADMIN)
        assert res.status_code == 200 and res.json()["counts"]["created"] == 1
        assert (await client.post("/api/ingest/portal", json={**body, "portal": "Bad Slug"}, headers=ADMIN)).status_code == 422
        bad = {**body, "items": [{**item("51").model_dump(), "price_etb": -5}]}
        assert (await client.post("/api/ingest/portal", json=bad, headers=ADMIN)).status_code == 422
        assert isinstance(client, httpx.AsyncClient)


def test_implausible_sizes_in_loose_text_are_skipped_for_the_first_plausible_one():
    html = "<html><title>Flat for sale in Bole</title><body><p>District: 226,000 m² of land. Studio of 2 m² gallery. This flat is 95 m² with 2 bedrooms.</p></body></html>"
    listing = extract_listing(html, "https://x.test/p/777-flat")
    assert listing.sqm == 95 and listing.bedrooms == 2


def test_a_size_from_loose_text_that_does_not_fit_the_price_is_dropped():
    html = ('<html><title>Flat for sale in Bole</title><script type="application/ld+json">{"@type":"RealEstateListing","offers":{"price":"8480560","priceCurrency":"ETB"}}</script>'
            "<body><p>2 bedrooms, 4,000 m² compound</p></body></html>")
    listing = extract_listing(html, "https://x.test/p/778-flat")
    assert listing.sqm is None and listing.price_etb == 8_480_560 and to_unit(listing, "X").startswith("missing size")
