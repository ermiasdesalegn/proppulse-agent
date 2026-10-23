from datetime import date
from types import SimpleNamespace

import pytest

from app import finance
from app.finance import GENERIC_PLAN, add_months, build_schedule, describe, fmt_etb, rank_by_reliability, resolve_plan, summarize

SIGNING = date(2026, 11, 9)
PRICE = 12_000_000


def plan(**over):
    return {**GENERIC_PLAN, **over}


def test_months_are_added_without_overflowing_short_months():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29), "leap year"
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)


def test_money_is_formatted_in_millions_or_plain_birr():
    assert fmt_etb(13_200_000) == "13.2M ETB" and fmt_etb(3_960_000) == "3.96M ETB" and fmt_etb(950_000) == "950,000 ETB"


# ---------------- which terms apply ----------------

def test_each_field_falls_back_on_its_own_unit_then_developer_then_generic():
    assert resolve_plan(None, None) == GENERIC_PLAN
    assert resolve_plan(None, {"installments": 24, "interval_months": 1, "handover_pct": 20}) == {"installments": 24, "interval_months": 1, "handover_pct": 20.0}
    mixed = resolve_plan({"installments": 4}, {"installments": 24, "interval_months": 2})
    assert mixed == {"installments": 4, "interval_months": 2, "handover_pct": 10.0}


def test_nonsense_terms_are_clamped_instead_of_trusted():
    odd = resolve_plan({"installments": -3, "interval_months": 0, "handover_pct": 400}, None)
    assert odd["installments"] == 0 and odd["interval_months"] == 1 and odd["handover_pct"] == 90.0


# ---------------- the schedule ----------------

def test_a_schedule_adds_up_to_exactly_the_price_and_the_percentages_to_100():
    pays = build_schedule(PRICE, 30, plan(installments=8, interval_months=3, handover_pct=10), signing=SIGNING, delivery=date(2028, 12, 31))
    assert sum(p.amount_etb for p in pays) == pytest.approx(PRICE, abs=0.01)
    assert sum(p.pct for p in pays) == pytest.approx(100.0)
    assert [p.label for p in pays][0] == "Down payment on signing" and pays[-1].label == "On handover"
    assert pays[0].amount_etb == pytest.approx(3_600_000) and pays[-1].amount_etb == pytest.approx(1_200_000)
    assert len(pays) == 10


def test_payments_are_in_date_order_and_none_falls_after_handover():
    delivery = date(2028, 6, 30)
    pays = build_schedule(PRICE, 25, plan(installments=24, interval_months=3), signing=SIGNING, delivery=delivery)
    dates = [p.due for p in pays]
    assert dates == sorted(dates) and max(dates) == delivery and dates[0] == SIGNING


def test_instalments_are_squeezed_in_when_delivery_is_sooner_than_the_plan_assumes():
    pays = build_schedule(PRICE, 20, plan(installments=8, interval_months=6), signing=SIGNING, delivery=date(2027, 5, 9))
    assert max(p.due for p in pays) == date(2027, 5, 9) and sum(p.amount_etb for p in pays) == pytest.approx(PRICE, abs=0.01)
    builds = [p for p in pays if p.label.startswith("Instalment")]
    assert len(builds) == 8 and all(p.due < date(2027, 5, 9) for p in builds)


def test_a_unit_that_is_already_delivered_gets_a_schedule_that_still_makes_sense():
    pays = build_schedule(PRICE, 30, plan(), signing=SIGNING, delivery=date(2026, 1, 1))
    assert sum(p.amount_etb for p in pays) == pytest.approx(PRICE, abs=0.01)
    assert [p.due for p in pays] == sorted(p.due for p in pays)


def test_no_instalments_means_down_payment_then_the_rest_on_handover():
    pays = build_schedule(PRICE, 30, plan(installments=0), signing=SIGNING, delivery=date(2027, 9, 1))
    assert [p.label for p in pays] == ["Down payment on signing", "On handover"]
    assert pays[1].amount_etb == pytest.approx(8_400_000)


def test_a_full_down_payment_leaves_nothing_to_schedule():
    pays = build_schedule(PRICE, 100, plan(), signing=SIGNING, delivery=date(2027, 9, 1))
    assert len(pays) == 1 and pays[0].amount_etb == pytest.approx(PRICE)


def test_a_missing_down_payment_percentage_uses_a_stated_default():
    assert build_schedule(PRICE, None, plan(), signing=SIGNING, delivery=date(2028, 1, 1))[0].pct == 20.0


def test_no_handover_share_ends_with_the_last_instalment():
    pays = build_schedule(PRICE, 30, plan(installments=4, interval_months=3, handover_pct=0), signing=SIGNING, delivery=date(2028, 1, 1))
    assert pays[-1].label == "Instalment 4 of 4" and sum(p.amount_etb for p in pays) == pytest.approx(PRICE, abs=0.01)


def test_payment_json_is_rounded_for_display():
    j = build_schedule(PRICE, 30, plan(), signing=SIGNING, delivery=date(2028, 1, 1))[0].to_json()
    assert j == {"label": "Down payment on signing", "due": "2026-11-09", "pct": 30.0, "amount_etb": 3_600_000}


# ---------------- summary ----------------

def test_the_summary_gives_the_numbers_buyers_compare():
    pays = build_schedule(PRICE, 30, plan(installments=8, interval_months=3, handover_pct=10), signing=SIGNING, delivery=date(2028, 11, 9))
    s = summarize(pays)
    assert s["down_payment_etb"] == 3_600_000 and s["installment_count"] == 8 and s["handover_etb"] == 1_200_000
    assert s["installment_etb"] == round((PRICE * 0.60) / 8) and s["months_to_handover"] == 24
    assert s["monthly_equivalent_etb"] == round((PRICE - 3_600_000) / 24)
    assert s["handover_date"] == "2028-11-09"


def test_the_description_reads_as_one_sentence():
    pays = build_schedule(PRICE, 30, plan(installments=8, interval_months=3, handover_pct=10), signing=SIGNING, delivery=date(2028, 11, 9))
    assert describe(summarize(pays), fmt_etb) == "3.6M ETB on signing, then 8 payments of 900,000 ETB and 1.2M ETB at handover"
    only_down = build_schedule(PRICE, 100, plan(), signing=SIGNING, delivery=None)
    assert describe(summarize(only_down), fmt_etb) == "12M ETB on signing"


# ---------------- developer reliability ----------------

def match(dev: str, similarity: float):
    return SimpleNamespace(developer_id=dev, similarity=similarity, name=f"{dev}-{similarity}")


def test_among_near_equal_matches_the_more_reliable_developer_comes_first():
    a, b = match("risky", 0.801), match("solid", 0.795)
    ranked = rank_by_reliability([a, b], {"risky": 0.4, "solid": 0.9})
    assert [m.developer_id for m in ranked] == ["solid", "risky"]


def test_the_reward_has_no_cliff_edges():
    """Two matches 0.006 apart are near-equal wherever they happen to sit on the scale."""
    for base in (0.65, 0.70, 0.78, 0.8, 0.9):
        a, b = match("risky", base + 0.006), match("solid", base)
        assert rank_by_reliability([a, b], {"risky": 0.4, "solid": 0.9})[0].developer_id == "solid"


def test_a_clearly_better_match_is_never_pushed_down_by_reputation():
    best, other = match("risky", 0.90), match("solid", 0.70)
    assert rank_by_reliability([other, best], {"risky": 0.0, "solid": 1.0})[0] is best, "0.20 apart: more than the largest possible reward"


def test_equal_reliability_keeps_similarity_order_and_unknown_developers_are_neutral():
    a, b, c = match("x", 0.80), match("y", 0.805), match("z", 0.79)
    assert [m.developer_id for m in rank_by_reliability([a, b, c], {})] == ["y", "x", "z"]
    assert [m.developer_id for m in rank_by_reliability([a, b, c], {"x": 0.7, "y": 0.7, "z": 0.7})] == ["y", "x", "z"]


def test_ranking_nothing_or_one_thing_is_fine():
    assert rank_by_reliability([], {}) == []
    only = match("x", 0.7)
    assert rank_by_reliability([only], {"x": 0.5}) == [only]


def test_module_constants_are_sane():
    assert 0 < finance.RELIABILITY_WEIGHT < 0.1 and finance.SIGNING_DELAY_DAYS >= 1
