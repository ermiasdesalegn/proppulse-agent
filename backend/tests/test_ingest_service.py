"""Ingestion on the real database (rolled back), with a fake model and a fake fetcher."""
import pytest

from app.config import get_settings
from app.events import EventBus
from app.ingest.fetch import FetchError
from app.ingest.models import ExtractedUnit, ListingExtraction, ScrapeProfile
from app.ingest.sample import PROFILE, sample_listing
from app.ingest.service import ExtractionError, IngestionService
from tests.agent_helpers import FakeGateway


class FakeFetcher:
    def __init__(self, pages=None, images=None):
        self.pages, self.images, self.requested = pages or {}, images or {}, []

    async def get_text(self, url):
        self.requested.append(url)
        if url not in self.pages:
            raise FetchError("the server answered 404")
        return self.pages[url]

    async def get_bytes(self, url, *, max_bytes=0):
        self.requested.append(url)
        if url not in self.images:
            raise FetchError("the server answered 404")
        return self.images[url]


def make(repo, gateway=None, fetcher=None, **settings):
    gw = gateway or FakeGateway()
    bus = EventBus()
    service = IngestionService(repo, gw, fetcher or FakeFetcher(), get_settings().model_copy(update=settings), events=bus)
    return service, gw, bus


async def ayat_units(db_conn, status=None):
    cur = await db_conn.execute("select title, price_etb, status, lat, lng, vision_amenities, vector_dims(embedding) d, content_hash from properties p "
                                "join developers d on d.id = p.developer_id where d.slug = 'ayat' order by title")
    return {r["title"]: r for r in await cur.fetchall()}


async def run(service, variant=1, **kw):
    html, profile = sample_listing(variant)
    return await service.run(developer_slug="ayat", source="html", html=html, profile=ScrapeProfile(**profile), **kw)


# ---------------- the sample matches the seed ----------------

async def test_the_baseline_sample_page_changes_nothing_because_every_unit_is_recognised(repo):
    service, gw, _ = make(repo)
    report = await run(service, 1, full_sync=True)
    assert report["found"] == 4 and report["unchanged"] == 4
    assert (report["created"], report["price_changed"], report["removed"], report["skipped"]) == ([], [], [], [])
    assert report["sync"] == "done" and gw.calls == [], "recognised units cost no model calls at all"


# ---------------- preview, then apply ----------------

async def test_a_preview_reports_every_change_and_touches_nothing(repo, db_conn):
    service, gw, bus = make(repo)
    before = await ayat_units(db_conn)
    report = await run(service, 2, full_sync=True, dry_run=True)

    assert [(c["title"], c["old_price_etb"], c["new_price_etb"], c["change_pct"]) for c in report["price_changed"]] == [("Ayat Heights 2BR Apartment", 6_800_000, 6_200_000, -8.82)]
    assert [c["title"] for c in report["created"]] == ["Ayat Garden Residences 2BR Townhouse"]
    assert [r["title"] for r in report["removed"]] == ["Ayat Square Studios Studio"] and report["sync"] == "preview"
    assert await ayat_units(db_conn) == before, "nothing written"
    assert gw.calls == [] and bus.subscribe(0).backlog == [] and "run_id" not in report
    cur = await db_conn.execute("select count(*) n from ingest_runs")
    assert (await cur.fetchone())["n"] == 0


async def test_applying_updates_prices_adds_the_new_unit_and_retires_the_missing_one(repo, db_conn):
    service, gw, bus = make(repo)
    report = await run(service, 2, full_sync=True)
    units = await ayat_units(db_conn)

    assert float(units["Ayat Heights 2BR Apartment"]["price_etb"]) == 6_200_000
    cur = await db_conn.execute("select old_price_etb, new_price_etb, change_pct from price_history ph join properties p on p.id = ph.property_id where p.title = 'Ayat Heights 2BR Apartment'")
    assert [(float(r["old_price_etb"]), float(r["new_price_etb"]), float(r["change_pct"])) for r in await cur.fetchall()] == [(6_800_000, 6_200_000, -8.82)]

    new = units["Ayat Garden Residences 2BR Townhouse"]
    assert new["status"] == "AVAILABLE" and new["d"] == 768 and float(new["price_etb"]) == 9_900_000
    assert new["vision_amenities"]["source"] == "scrape"
    sibling = units["Ayat Garden Residences 3BR Townhouse"]
    assert (new["lat"], new["lng"]) == (sibling["lat"], sibling["lng"]), "a new unit of an existing project takes that project's map position"
    assert units["Ayat Square Studios Studio"]["status"] == "UNAVAILABLE"
    assert units["Ayat Hill Villas 4BR Villa"]["status"] == "AVAILABLE"

    assert [c[0] for c in gw.calls] == ["embed_documents"], "only the one new unit was embedded; the price cut used no model"
    event = [e for e in bus.subscribe(0).backlog if e["kind"] == "ingest"][0]
    assert (event["created"], event["price_changed"], event["removed"]) == (1, 1, 1)
    assert report["run_id"] and report["counts"] == {"created": 1, "price_changed": 1, "removed": 1, "unchanged": 2, "skipped": 0}


async def test_running_the_same_page_twice_changes_nothing_the_second_time(repo):
    service, _, _ = make(repo)
    await run(service, 2, full_sync=True)
    again = await run(service, 2, full_sync=True)
    assert again["counts"] == {"created": 0, "price_changed": 0, "removed": 0, "unchanged": 4, "skipped": 0}


async def test_a_unit_that_reappears_is_made_available_again_and_a_price_rise_is_recorded(repo, db_conn):
    service, _, _ = make(repo)
    await run(service, 2, full_sync=True)
    back = await run(service, 1, full_sync=True)
    assert [r["title"] for r in back["reactivated"]] == ["Ayat Square Studios Studio"]
    assert [c["change_pct"] for c in back["price_changed"]] == [pytest.approx(9.68, abs=0.01)]
    units = await ayat_units(db_conn)
    assert units["Ayat Square Studios Studio"]["status"] == "AVAILABLE"
    assert [r["title"] for r in back["removed"]] == ["Ayat Garden Residences 2BR Townhouse"], "the unit from the other variant is retired in turn"


# ---------------- floor plans ----------------

def page_with_plans(*urls):
    cards = "".join(f'<article class="unit"><h3 class="project">Ayat New Court {i}</h3><p class="type">2BR Apartment</p><p class="area">Ayat</p>'
                    f'<p class="price">ETB 8.1M</p><p class="size">{90 + i} sqm</p><img class="plan" src="{u}"></article>' for i, u in enumerate(urls))
    profile = ScrapeProfile(item="article.unit", fields={**PROFILE.fields, "image": "img.plan@src"})
    return f"<html><body>{cards}</body></html>", profile


async def test_a_new_unit_with_a_floor_plan_is_read_by_the_vision_model_and_its_tags_are_searchable(repo, db_conn):
    html, profile = page_with_plans("https://dev.example/plans/a.png")
    fetcher = FakeFetcher(images={"https://dev.example/plans/a.png": (b"\x89PNG-bytes", "image/png")})
    service, gw, _ = make(repo, fetcher=fetcher)
    report = await service.run(developer_slug="ayat", source="html", html=html, profile=profile)

    assert [c[0] for c in gw.calls] == ["image", "embed_documents"]
    assert report["created"][0]["floor_plan_tags"] == ["open-plan kitchen", "maid room + bath"]
    unit = (await ayat_units(db_conn))["Ayat New Court 0 2BR Apartment"]
    assert unit["vision_amenities"]["tags"] == ["open-plan kitchen", "maid room + bath"] and unit["vision_amenities"]["net_to_gross_ratio"] == 0.82
    assert "open-plan kitchen" in gw.documents[0], "the tags are part of what gets embedded, so a buyer can find the unit by them"


async def test_an_unreadable_floor_plan_never_blocks_the_unit(repo, db_conn):
    html, profile = page_with_plans("https://dev.example/missing.png")
    service, gw, _ = make(repo)  # the fetcher knows no images
    report = await service.run(developer_slug="ayat", source="html", html=html, profile=profile)
    assert report["counts"]["created"] == 1 and any("Could not read the floor plan" in n for n in report["notes"])
    assert (await ayat_units(db_conn))["Ayat New Court 0 2BR Apartment"]["vision_amenities"]["tags"] == []

    gw2 = FakeGateway()
    gw2.fail_image = True
    fetcher = FakeFetcher(images={"https://dev.example/b.png": (b"x", "image/png")})
    service2, _, _ = make(repo, gateway=gw2, fetcher=fetcher)
    html2, profile2 = page_with_plans("https://dev.example/b.png")
    assert (await service2.run(developer_slug="ayat", source="html", html=html2.replace("Court 0", "Court 9"), profile=profile2))["counts"]["created"] == 1


async def test_the_per_run_vision_limit_protects_the_model_quota(repo):
    html, profile = page_with_plans("https://dev.example/1.png", "https://dev.example/2.png", "https://dev.example/3.png")
    fetcher = FakeFetcher(images={f"https://dev.example/{i}.png": (b"x", "image/png") for i in (1, 2, 3)})
    service, gw, _ = make(repo, fetcher=fetcher, ingest_max_vision_per_run=1)
    report = await service.run(developer_slug="ayat", source="html", html=html, profile=profile)
    assert [c[0] for c in gw.calls].count("image") == 1 and report["counts"]["created"] == 3
    assert sum("per-run limit" in n for n in report["notes"]) == 2


# ---------------- announcements read by the model ----------------

def announcement_units():
    return ListingExtraction(units=[ExtractedUnit(project_name="Ayat Lake View", unit_type="3BR Apartment", sub_city="ayat", bedrooms=3, sqm=130, price_etb=11_900_000,
                                                  down_payment_pct=25, delivery_date="Q3 2027")])


async def test_an_announcement_is_read_by_the_model_and_marked_as_such(repo, db_conn):
    gw = FakeGateway()
    gw.listing_extraction = announcement_units()
    service, _, _ = make(repo, gateway=gw)
    report = await service.run(developer_slug="ayat", source="text", text="Ayat announces the Lake View 3BR, 130 sqm, ETB 11.9M, 25% down.")
    assert report["counts"]["created"] == 1 and [c[0] for c in gw.calls] == ["extraction", "embed_documents"]
    unit = (await ayat_units(db_conn))["Ayat Lake View 3BR Apartment"]
    assert unit["vision_amenities"]["source"] == "announcement"
    cur = await db_conn.execute("select sub_city from properties where title = 'Ayat Lake View 3BR Apartment'")
    assert (await cur.fetchone())["sub_city"] == "Ayat", "area names are normalised"


async def test_one_bad_unit_from_the_model_is_reported_and_the_rest_are_kept(repo, db_conn):
    gw = FakeGateway()
    gw.listing_extraction = ListingExtraction(units=[
        ExtractedUnit(project_name="Ayat Lake View", unit_type="3BR Apartment", sub_city="Ayat", bedrooms=3, sqm=130, price_etb=11_900_000, delivery_date="Q3 2027"),
        ExtractedUnit(project_name="Ayat Broken", unit_type="Studio", sub_city="Ayat", bedrooms=0, sqm=40, price_etb=0),       # a price of zero
        ExtractedUnit(project_name="Ayat Huge", unit_type="Villa", sub_city="Ayat", bedrooms=4, sqm=-5, price_etb=9_000_000),  # a negative size
    ])
    service, _, _ = make(repo, gateway=gw)
    report = await service.run(developer_slug="ayat", source="text", text="Three units announced.")
    assert report["counts"]["created"] == 1 and len(report["parse_errors"]) == 2
    assert report["parse_errors"][0] == "Ayat Broken Studio: price_etb is missing or out of range"
    cur = await db_conn.execute("select delivery_date from properties where title = 'Ayat Lake View 3BR Apartment'")
    assert str((await cur.fetchone())["delivery_date"]) == "2027-09-30", "'Q3 2027' as written becomes a real date"


async def test_an_announcement_never_retires_units_unless_asked(repo):
    gw = FakeGateway()
    gw.listing_extraction = announcement_units()
    service, _, _ = make(repo, gateway=gw)
    report = await service.run(developer_slug="ayat", source="text", text="Something new.")
    assert report["removed"] == [] and report["sync"] == "off"


async def test_a_model_outage_is_a_clear_error_and_blank_text_is_refused(repo):
    gw = FakeGateway()
    gw.fail_listing_extraction = True
    service, _, _ = make(repo, gateway=gw)
    with pytest.raises(ExtractionError, match="language model"):
        await service.run(developer_slug="ayat", source="text", text="x")
    with pytest.raises(ValueError):
        await service.run(developer_slug="ayat", source="text", text="   ")


# ---------------- safety ----------------

async def test_a_thin_page_cannot_wipe_the_catalogue(repo, db_conn):
    service, _, _ = make(repo)
    html, profile = sample_listing(1)
    only_one = html.split("</article>")[0] + "</article></main></body></html>"
    report = await service.run(developer_slug="ayat", source="html", html=only_one, profile=ScrapeProfile(**profile), full_sync=True)
    assert report["sync"].startswith("skipped: only 1 of 4") and report["removed"] == []
    assert all(u["status"] == "AVAILABLE" for u in (await ayat_units(db_conn)).values())


async def test_an_empty_result_removes_nothing_and_blames_the_selectors(repo, db_conn):
    service, _, _ = make(repo)
    report = await service.run(developer_slug="ayat", source="html", html="<html><body><p>Coming soon</p></body></html>", profile=ScrapeProfile(**sample_listing(1)[1]), full_sync=True)
    assert report["sync"].startswith("skipped: the page returned no units") and any("layout may have changed" in n for n in report["notes"])
    assert all(u["status"] == "AVAILABLE" for u in (await ayat_units(db_conn)).values())


async def test_syncing_one_developer_never_touches_another(repo, db_conn):
    service, _, _ = make(repo)
    await run(service, 2, full_sync=True)
    cur = await db_conn.execute("select count(*) n from properties p join developers d on d.id = p.developer_id where d.slug <> 'ayat' and p.status <> 'AVAILABLE'")
    assert (await cur.fetchone())["n"] == 0


async def test_unreadable_cards_are_reported_but_the_rest_are_applied(repo):
    service, _, _ = make(repo)
    html, profile = sample_listing(1)
    broken = html.replace("ETB 6,800,000", "Call for price")
    report = await service.run(developer_slug="ayat", source="html", html=broken, profile=ScrapeProfile(**profile))
    assert report["parse_errors"] == ["item 2: missing or unreadable price"] and report["unchanged"] == 3


async def test_the_same_card_twice_is_counted_once(repo):
    service, _, _ = make(repo)
    html, profile = sample_listing(1)
    first_card = html.split("<article")[1].split("</article>")[0]
    doubled = html.replace("</main>", f"<article{first_card}</article></main>")
    report = await service.run(developer_slug="ayat", source="html", html=doubled, profile=ScrapeProfile(**profile))
    assert report["skipped"] == [{"title": "Ayat Garden Residences 3BR Townhouse", "reason": "listed twice on the page"}] and report["unchanged"] == 4


# ---------------- sources and input checks ----------------

async def test_a_saved_scrape_address_and_selectors_are_used_for_scheduled_runs(repo):
    html, profile = sample_listing(2)
    fetcher = FakeFetcher(pages={"https://ayat.example/units": html})
    service, _, _ = make(repo, fetcher=fetcher)
    await repo.set_developer_scrape("ayat", "https://ayat.example/units", profile)
    report = await service.run(developer_slug="ayat", source="url", full_sync=True, dry_run=True)
    assert fetcher.requested == ["https://ayat.example/units"] and report["counts"]["price_changed"] == 1


async def test_fetch_problems_and_bad_input_are_clear_errors(repo):
    service, _, _ = make(repo)
    with pytest.raises(FetchError):
        await service.run(developer_slug="ayat", source="url", url="https://nowhere.example/")
    with pytest.raises(ValueError, match="no address"):
        await service.run(developer_slug="gift", source="url")
    with pytest.raises(LookupError):
        await service.run(developer_slug="nobody", source="html", html="<p>x</p>")
    with pytest.raises(ValueError):
        await service.run(developer_slug="ayat", source="html", html="")
    with pytest.raises(ValueError):
        await service.run(developer_slug="ayat", source="carrier-pigeon")


async def test_a_page_without_selectors_is_read_by_the_model(repo):
    gw = FakeGateway()
    gw.listing_extraction = announcement_units()
    service, _, _ = make(repo, gateway=gw)
    report = await service.run(developer_slug="gift", source="html", html="<html><body><main>Lake View 3BR 130 sqm 11.9M</main></body></html>")
    assert any("language model" in n for n in report["notes"]) and gw.calls[0][0] == "extraction"
