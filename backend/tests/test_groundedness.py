import pytest

from app.groundedness import allowed_values, extract_claims, ungrounded

FACTS = {"title": "Noah Bole Towers 2BR", "price_etb": 13200000.0, "sqm": 96.0, "price_per_sqm_etb": 137500, "down_payment_pct": 30.0,
         "down_payment_etb": 3960000, "delivery_date": "2027-03-31", "payment_plan": "3.96M ETB on signing, then 24 payments of 275,000 ETB"}


def values(text):
    return [(c.text, c.value) for c in extract_claims(text)]


def test_figures_with_units_and_large_numbers_are_claims_but_small_bare_ones_are_not():
    assert values("Option 2 has 3 bedrooms, ready in 10:00 days") == []
    got = dict(values("13.2M ETB, 137,500 ETB/sqm, 30% down, 96 sqm, delivery 2027"))
    assert got == {"13.2M": 13_200_000, "137,500 ETB": 137_500, "30%": 30, "96 sqm": 96, "2027": 2027}


def test_million_and_thousand_suffixes_scale():
    assert values("about 2.5 million birr or 800k")[0][1] == 2_500_000 and values("800k")[0][1] == 800_000


def test_a_reply_that_restates_the_facts_is_grounded_however_it_formats_them():
    reply = "It is 13.2M ETB (137,500 ETB/sqm), 30% down which is 3.96M ETB, 96 sqm, delivery 2027. Then 24 payments of 275,000 ETB."
    assert ungrounded(reply, FACTS) == []
    assert ungrounded("Priced at 13,200,000 ETB, down payment 3,960,000 birr.", FACTS) == []


def test_an_invented_price_is_caught():
    bad = ungrounded("This one is a bargain at 11.5M ETB with a 40% discount.", FACTS)
    assert [c.value for c in bad] == [11_500_000, 40]


def test_rounding_to_what_was_written_is_tolerated_but_a_wrong_digit_is_not():
    assert ungrounded("about 13.2M", FACTS) == []
    assert ungrounded("about 13M", FACTS) == [], "13M is 13.2M written to the nearest million"
    assert [c.value for c in ungrounded("13.9M", FACTS)] == [13_900_000]
    assert ungrounded("137.5k per sqm", FACTS) == []


def test_numbers_the_buyer_gave_are_allowed_back():
    assert ungrounded("Your budget of 15M ETB works, and I'll call +251911000000.", FACTS, "I have 15 million birr, call +251911000000") == []
    assert [c.value for c in ungrounded("Your budget of 15M ETB works", FACTS)] == [15_000_000]


def test_facts_can_be_nested_structures_and_numbers():
    assert ungrounded("1,850,000 a month", {"matrix": [{"monthly": 1850000}]}) == []
    assert allowed_values({"a": [1, 2.5, "3,000 sqm"]}) == [1.0, 2.5, 3000.0]


def test_empty_and_numberless_text_is_trivially_grounded():
    assert ungrounded("", FACTS) == [] and ungrounded("Welcome! Tell me your budget.", FACTS) == []


@pytest.mark.parametrize("text", ["a 5% bonus", "free for 99 sqm extra", "worth 20 million"])
def test_unit_bearing_numbers_are_checked_even_when_small(text):
    assert ungrounded(text, FACTS)
