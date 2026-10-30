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


# ---------------- small developer sites: sitemap seeding and bot challenges ----------------

class PageFetcher:
    def __init__(self, pages):
        self.pages, self.requested = pages, []

    async def get_text(self, url):
        from app.ingest.fetch import FetchError

        self.requested.append(url)
        if url not in self.pages:
            raise FetchError("the server answered 404")
        return self.pages[url]


SITEMAP_INDEX = ('<sitemapindex><sitemap><loc>https://dev.test/estate_property-sitemap.xml</loc></sitemap>'
                 '<sitemap><loc>https://dev.test/post_tag-sitemap.xml</loc></sitemap></sitemapindex>')
PROPERTY_SITEMAP = '<urlset><url><loc>https://dev.test/apartment-for-sale/aware-one-bedroom/</loc></url><url><loc>https://dev.test/apartment-for-sale/sarbet-two-bedroom/</loc></url><url><loc>https://dev.test/blog/news/</loc></url></urlset>'


async def test_the_sites_own_sitemap_seeds_the_crawl_and_skips_taxonomy_files():
    from app.ingest.projects import crawl, sitemap_urls

    fetcher = PageFetcher({
        "https://dev.test/robots.txt": "User-agent: *\nSitemap: https://dev.test/sitemap_index.xml\n",
        "https://dev.test/sitemap_index.xml": SITEMAP_INDEX, "https://dev.test/estate_property-sitemap.xml": PROPERTY_SITEMAP,
        "https://dev.test/": "<html><body>home</body></html>",
        "https://dev.test/apartment-for-sale/aware-one-bedroom/": "<html><body>one</body></html>",
        "https://dev.test/apartment-for-sale/sarbet-two-bedroom/": "<html><body>two</body></html>",
    })
    assert await sitemap_urls(fetcher, "https://dev.test/") == ["https://dev.test/apartment-for-sale/aware-one-bedroom/", "https://dev.test/apartment-for-sale/sarbet-two-bedroom/"]
    assert "https://dev.test/post_tag-sitemap.xml" not in fetcher.requested
    pages, notes = await crawl(fetcher, "https://dev.test/")
    assert {p.url for p in pages} == {"https://dev.test/", "https://dev.test/apartment-for-sale/aware-one-bedroom/", "https://dev.test/apartment-for-sale/sarbet-two-bedroom/"}


async def test_a_site_without_a_sitemap_is_still_crawled_by_its_links():
    from app.ingest.projects import crawl

    fetcher = PageFetcher({"https://small.test/": '<html><body><a href="/gallery">g</a></body></html>', "https://small.test/gallery": "<html><body>gallery</body></html>"})
    pages, _ = await crawl(fetcher, "https://small.test/")
    assert [p.url for p in pages] == ["https://small.test/", "https://small.test/gallery"]


async def test_a_bot_check_page_ends_the_crawl_with_a_clear_note_and_is_not_bypassed():
    from app.ingest.projects import crawl

    interstitial = "<!DOCTYPE html><html><head><script>setTimeout(function(){window.location.reload();}, 5000);</script><title>One moment, please...</title></head></html>"
    fetcher = PageFetcher({"https://blocked.test/listing": interstitial})
    pages, notes = await crawl(fetcher, "https://blocked.test/listing")
    assert pages == [] and "bot-protection" in notes[0] and "not bypassed" in notes[0]
    assert fetcher.requested.count("https://blocked.test/listing") == 1, "no retry loop against the challenge"


# ---------------- retiring listings that vanished from a complete portal crawl ----------------

async def test_a_complete_crawl_retires_listings_that_vanished_and_never_wipes_the_catalogue(repo, db_conn):
    service, _, _ = make(repo)
    await service.run_portal(portal="sync-test", name="Sync Test", items=[item(str(n), url=f"https://s.test/{n}", sqm=100 + n) for n in range(1, 7)])
    wiped = await service.sync_portal(portal="sync-test", seen_refs=["1"])
    assert wiped["removed"] == [] and wiped["sync"].startswith("skipped: only 1 of 6")
    preview = await service.sync_portal(portal="sync-test", seen_refs=["1", "2", "3", "4"], dry_run=True)
    assert len(preview["removed"]) == 2 and preview["sync"] == "preview"
    done = await service.sync_portal(portal="sync-test", seen_refs=["1", "2", "3", "4"])
    assert done["sync"] == "done" and len(done["removed"]) == 2
    cur = await db_conn.execute("select count(*) n from properties p join developers d on d.id = p.developer_id where d.slug = 'sync-test' and p.status = 'UNAVAILABLE'")
    assert (await cur.fetchone())["n"] == 2
    assert (await service.sync_portal(portal="sync-test", seen_refs=[]))["sync"].startswith("skipped: the crawl saw no listings")


async def test_the_sync_endpoint_needs_the_admin_key(spy_repo):
    client, _ = await build(spy_repo)
    async with client:
        assert (await client.post("/api/ingest/portal/sync", json={"portal": "epc-test", "seen_refs": []})).status_code == 401
        assert (await client.post("/api/ingest/portal/sync", json={"portal": "nobody-here", "seen_refs": ["1"]}, headers=ADMIN)).status_code == 404


# ---------------- the spider decides when a sync is safe ----------------

def spider(**args):
    from unittest.mock import MagicMock

    from scraper.spiders.portals import PortalSpider

    s = PortalSpider(site="guzohomes", **args)
    s.crawler = MagicMock()
    return s


def test_a_sync_is_only_safe_after_a_full_clean_crawl():
    s = spider()
    s.taken["guzohomes"] = s.done["guzohomes"] = 10
    assert s.sync_ready("guzohomes")
    s.done["guzohomes"] = 9                      # the run was cut short (time limit, shutdown)
    assert not s.sync_ready("guzohomes")
    s.done["guzohomes"] = 10
    s.failed["guzohomes"] = 1                    # a page errored: its absence proves nothing
    assert not s.sync_ready("guzohomes")
    s.failed["guzohomes"] = 0
    s.stopped["guzohomes"] = "the site answered 429"
    assert not s.sync_ready("guzohomes")
    assert not spider(limit="50").sync_ready("guzohomes") and not spider(since="2026-10-01").sync_ready("guzohomes")


def test_the_spider_refuses_unknown_sites_and_skips_the_browser_site_without_playwright(monkeypatch):
    monkeypatch.delenv("PORTAL_PLAYWRIGHT", raising=False)
    from scraper.spiders.portals import PortalSpider

    with pytest.raises(ValueError, match="unknown site"):
        PortalSpider(site="nope")

    assert "ethiohome" not in [s.slug for s in PortalSpider(site="all").sites]
    with pytest.raises(ValueError, match="PORTAL_PLAYWRIGHT"):
        PortalSpider(site="ethiohome")
    monkeypatch.setenv("PORTAL_PLAYWRIGHT", "1")
    assert "ethiohome" in [s.slug for s in PortalSpider(site="all").sites]


# ---------------- found on the first real crawl ----------------

def test_a_price_per_square_metre_that_is_far_too_low_is_refused_as_a_rent_or_a_typo():
    from app.ingest.portal import PortalListing

    villa = PortalListing(url="https://x.test/p/1", ref="1", title="Furnished Villa in Bole", price_etb=640_000, sqm=700, bedrooms=4, sub_city="Bole", deal="sale")
    assert "per m²" in to_unit(villa, "X") and "rent or a typo" in to_unit(villa, "X")
    flat = PortalListing(url="https://x.test/p/2", ref="2", title="Flat in Bole", price_etb=5_200_000, sqm=95, bedrooms=2, sub_city="Bole", deal="sale")
    assert not isinstance(to_unit(flat, "X"), str)
    plot = PortalListing(url="https://x.test/p/3", ref="3", title="House in Ayat", price_etb=10_000_000, sqm=1500, bedrooms=4, sub_city="Ayat", deal="sale")
    assert not isinstance(to_unit(plot, "X"), str), "a house on a big plot is cheap per m² and still a sale"


async def test_units_read_by_the_model_keep_the_pages_address_and_get_the_same_price_check(repo, db_conn):
    from tests.agent_helpers import FakeGateway

    gw = FakeGateway()
    gw.listing_extraction = ListingExtraction(units=[
        ExtractedUnit(project_name="p", unit_type="2BR Apartment", sub_city="Bole", bedrooms=2, sqm=100, price_etb=9_500_000),
        ExtractedUnit(project_name="p", unit_type="Villa", sub_city="Bole", bedrooms=4, sqm=700, price_etb=640_000),
    ])
    service, _, _ = make(repo, gateway=gw)
    report = await service.run_portal(portal="model-src", name="Model Src", items=[item("60", price=None, url="https://epc.test/for-sale/60", text="2 bedroom, 100 sqm, 9.5 million birr")])
    assert report["counts"]["created"] == 1 and "per m²" in report["skipped"][0]["reason"]
    cur = await db_conn.execute("select source_url from properties p join developers d on d.id = p.developer_id where d.slug = 'model-src'")
    assert [r["source_url"] for r in await cur.fetchall()] == ["https://epc.test/for-sale/60"]


# ---------------- the listing's own photo ----------------

LOGO_AND_TRANSFER_STATE = (
    '<html><head><meta property="og:image" content="https://cdn.test/bg/le-logo.webp"></head><body>'
    '<script type="application/json">{&q;p&q;:{&q;title&q;:&q;3BR Apartment&q;,&q;price&q;:9000000,&q;bedrooms&q;:3,&q;area&q;:120,'
    '&q;images&q;:[{&q;url&q;:&q;https://cdn.test/second.webp&q;,&q;cover&q;:false},{&q;url&q;:&q;https://cdn.test/front.webp&q;,&q;cover&q;:true}]}}</script>'
    '</body></html>'
)


def test_the_photo_the_site_marks_as_cover_is_kept_and_a_logo_never_is():
    listing = extract_listing(LOGO_AND_TRANSFER_STATE, "https://le.test/site/property-details/2547")
    assert listing.image_url == "https://cdn.test/front.webp" and listing.found_by["image_url"] == "embedded-json"
    only_logo = extract_listing('<html><head><meta property="og:image" content="https://cdn.test/icon/logo_white.webp"></head></html>', "https://le.test/p/1")
    assert only_logo.image_url is None


def test_the_photo_travels_from_the_crawler_to_the_unit_and_an_odd_address_is_dropped_not_fatal():
    assert item("7", image_url="javascript:alert(1)").image_url is None and item("8", image_url="https://p.test/x.jpg").image_url
    listing = extract_listing(LOGO_AND_TRANSFER_STATE, "https://le.test/site/property-details/2547")
    listing.sub_city, listing.sqm = "Bole", 120
    unit = to_unit(listing, "Live Ethio")
    assert not isinstance(unit, str) and unit.photo_url == "https://cdn.test/front.webp"


async def test_a_crawl_stores_the_photo_and_a_later_crawl_updates_it(repo, db_conn):
    service, _, _ = make(repo)
    await service.run_portal(portal="photo-test", name="Photo Test", items=[item("1", url="https://p.test/1", image_url="https://p.test/a.jpg")])
    await service.run_portal(portal="photo-test", name="Photo Test", items=[item("1", url="https://p.test/1", image_url="https://p.test/b.jpg")])
    cur = await db_conn.execute("select p.photo_url from properties p join developers d on d.id = p.developer_id where d.slug = 'photo-test'")
    assert [r["photo_url"] for r in await cur.fetchall()] == ["https://p.test/b.jpg"]
