from datetime import date

import pytest

from app.ingest.models import ScrapeProfile
from app.ingest.parse import (
    page_text,
    parse_area,
    parse_bedrooms,
    parse_date,
    parse_float,
    parse_listing_html,
    parse_money,
    parse_percent,
)


@pytest.mark.parametrize("text,expected", [
    ("14,500,000 ETB", 14_500_000), ("ETB 14.5M", 14_500_000), ("14.5 million birr", 14_500_000), ("Br. 14,500,000.00", 14_500_000),
    ("From 6.8M", 6_800_000), ("6,800 k", 6_800_000), ("3.6 Million", 3_600_000), ("14 500 000", 14_500_000), (" 14 500 000 ETB", 14_500_000),
    ("850000", 850_000), ("Price: 12.6m", 12_600_000), ("ETB 450 thousand", 450_000),
    ("Contact us", None), ("", None), (None, None), ("0", None),
])
def test_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("165 sqm", 165), ("165m²", 165), ("165 sq.m", 165), ("1,200 sq ft", 111.48), ("88.5 m2", 88.5), ("Studio", None), (None, None),
])
def test_area(text, expected):
    assert parse_area(text) == pytest.approx(expected) if expected else parse_area(text) is None


@pytest.mark.parametrize("text,expected", [
    ("3BR Apartment", 3), ("3 bedrooms", 3), ("Three bedroom villa", 3), ("two-bed", 2), ("Studio", 0), ("studio apartment", 0), ("4", 4), ("Penthouse 4BR", 4),
    ("Villa", None), (None, None),
])
def test_bedrooms(text, expected):
    assert parse_bedrooms(text) == expected


@pytest.mark.parametrize("text,expected", [("30%", 30), ("25 percent", 25), ("12.5%", 12.5), ("150%", None), ("n/a", None), (None, None)])
def test_percent(text, expected):
    assert parse_percent(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("2027-06-30", date(2027, 6, 30)), ("30 June 2027", date(2027, 6, 30)), ("June 2027", date(2027, 6, 30)), ("Jun 2027", date(2027, 6, 30)),
    ("Feb 2028", date(2028, 2, 29)), ("Q2 2027", date(2027, 6, 30)), ("Q4 2026", date(2026, 12, 31)), ("2027", date(2027, 12, 31)),
    ("Mar-2027", date(2027, 3, 31)), ("31 Feb 2027", None), ("soon", None), ("2027-13-40", None), (None, None),
])
def test_dates(text, expected):
    assert parse_date(text) == expected


def test_floats():
    assert parse_float("9.0235") == 9.0235 and parse_float("lat: -1.5") == -1.5 and parse_float("none") is None


PROFILE = ScrapeProfile(
    item=".unit",
    fields={"project_name": ".project", "unit_type": ".type", "sub_city": ".area", "price": ".price", "sqm": ".size", "down_payment": ".down",
            "delivery": ".delivery", "description": ".desc", "image": "img.plan@src", "lat": "@data-lat", "lng": "@data-lng"},
    defaults={"sub_city": "Bole"},
)

CARD = """<div class="unit" data-lat="9.02" data-lng="38.86">
  <span class="project">Ayat Garden Residences</span><span class="type">3BR Townhouse</span><span class="area">ayat</span>
  <span class="price">ETB 14.5M</span><span class="size">165 sqm</span><span class="down">30%</span><span class="delivery">June 2027</span>
  <p class="desc">Family townhouse with a garden.</p><img class="plan" src="/plans/garden.png"></div>"""


def test_a_full_card_is_read_completely():
    result = parse_listing_html(f"<html><body>{CARD}</body></html>", PROFILE, base_url="https://dev.example/listings")
    (u,) = result.units
    assert result.items_found == 1 and result.errors == []
    assert (u.project_name, u.unit_type, u.sub_city, u.bedrooms, u.sqm, u.price_etb) == ("Ayat Garden Residences", "3BR Townhouse", "Ayat", 3, 165, 14_500_000)
    assert (u.down_payment_pct, u.delivery_date, u.lat, u.lng) == (30, date(2027, 6, 30), 9.02, 38.86)
    assert u.floor_plan_image_url == "https://dev.example/plans/garden.png", "relative image links are resolved against the page address"
    assert u.display_title == "Ayat Garden Residences 3BR Townhouse"


def test_the_cards_own_picture_is_used_when_the_profile_names_no_image_selector():
    no_image = ScrapeProfile(item=PROFILE.item, fields={k: v for k, v in PROFILE.fields.items() if k != "image"}, defaults=PROFILE.defaults)
    card = CARD.replace('<img class="plan" src="/plans/garden.png">', '<img src="data:image/gif;base64,AAAA"><img data-src="/photos/front.jpg">')
    (u,) = parse_listing_html(card, no_image, base_url="https://dev.example/listings").units
    assert u.floor_plan_image_url == "https://dev.example/photos/front.jpg", "inline placeholders are skipped; lazy-loaded pictures are found"


def test_defaults_fill_a_missing_area_and_bedrooms_come_from_the_unit_type():
    card = CARD.replace('<span class="area">ayat</span>', "")
    (u,) = parse_listing_html(card, PROFILE).units
    assert u.sub_city == "Bole" and u.bedrooms == 3


def test_unreadable_cards_are_reported_and_skipped_but_good_ones_still_load():
    broken_price = CARD.replace("ETB 14.5M", "Contact us")
    no_size = CARD.replace("165 sqm", "")
    page = f"{CARD}{broken_price}{no_size}"
    result = parse_listing_html(page, PROFILE)
    assert result.items_found == 3 and len(result.units) == 1
    assert result.errors == ["item 2: missing or unreadable price", "item 3: missing or unreadable size"]


def test_out_of_range_values_are_reported_not_crashed_on():
    result = parse_listing_html(CARD.replace("30%", "30%").replace("ETB 14.5M", "ETB 999999999999999M"), PROFILE)
    assert result.units == [] and "out of range" in result.errors[0]


def test_a_page_with_no_matching_cards_is_an_empty_result_not_an_error():
    result = parse_listing_html("<html><body><p>Coming soon</p></body></html>", PROFILE)
    assert (result.items_found, result.units, result.errors) == (0, [], [])


def test_an_invalid_item_selector_is_a_clear_error():
    with pytest.raises(ValueError, match="not valid CSS"):
        parse_listing_html("<div></div>", ScrapeProfile(item="div[[["))


def test_markup_inside_a_field_becomes_plain_text_and_scripts_are_dropped():
    card = CARD.replace("Ayat Garden Residences", "<script>alert(1)</script>Evil <b>Co</b><style>.x{}</style>")
    (u,) = parse_listing_html(card, PROFILE).units
    assert u.project_name == "Evil Co"


def test_page_text_drops_scripts_styles_and_navigation_and_is_capped():
    html = "<html><head><style>.x{}</style></head><body><nav>Menu</nav><script>var a=1</script><main>3BR in Bole for 14.5M</main><footer>(c)</footer></body></html>"
    assert page_text(html) == "3BR in Bole for 14.5M"
    assert len(page_text("<p>" + "word " * 10_000 + "</p>", limit=100)) == 100
