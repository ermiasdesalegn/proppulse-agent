"""Pure logic: no database, no network."""
import pytest

from app.agent.nodes import can_relax, fmt_etb, relaxed_scope, should_relax_constraints
from app.agent.scoring import lead_score
from app.geo import adjacent_to, expand_with_adjacent, normalize_sub_cities

PREFS = {"max_budget_etb": 14_000_000.0, "min_bedrooms": 3, "preferred_sub_cities": ["Bole"]}


def test_relaxation_step_1_adds_12_percent_and_neighbouring_areas():
    s = relaxed_scope(PREFS, 1, 12.0, 2)
    assert s["effective_max_budget_etb"] == pytest.approx(15_680_000)
    assert s["effective_sub_cities"] == ["Bole", "Kazanchis", "CMC", "Sarbet"]
    assert s["relaxation_step"] == 1 and s["max_relaxation_steps"] == 2


def test_relaxation_step_2_adds_24_percent_and_drops_the_area_filter():
    s = relaxed_scope(PREFS, 2, 12.0, 2)
    assert s["effective_max_budget_etb"] == pytest.approx(17_360_000)
    assert s["effective_sub_cities"] == []


def test_relaxation_never_mutates_the_buyers_preferences():
    before = {**PREFS, "preferred_sub_cities": list(PREFS["preferred_sub_cities"])}
    relaxed_scope(PREFS, 1, 12.0, 2)
    relaxed_scope(PREFS, 2, 12.0, 2)
    assert PREFS == before


def test_relaxation_without_a_budget_leaves_it_open():
    s = relaxed_scope({"max_budget_etb": None, "min_bedrooms": 2, "preferred_sub_cities": ["Bole"]}, 1, 12.0, 2)
    assert s["effective_max_budget_etb"] is None


def test_can_relax_needs_a_budget_or_an_area():
    assert can_relax(PREFS)
    assert can_relax({"max_budget_etb": 5.0, "preferred_sub_cities": []})
    assert can_relax({"max_budget_etb": None, "preferred_sub_cities": ["Bole"]})
    assert not can_relax({"max_budget_etb": None, "min_bedrooms": 3, "preferred_sub_cities": []})


def _state(matches, step, prefs=PREFS, intent="search"):
    return {"active_matches": matches, "buyer_preferences": prefs, "intent": intent,
            "search_scope": {"relaxation_step": step, "max_relaxation_steps": 2}}


def test_edge_routes_hits_relax_and_waitlist_correctly():
    hit = [{"id": "x"}]
    assert should_relax_constraints(_state(hit, 0)) == "recommend"
    assert should_relax_constraints(_state(hit, 2)) == "recommend"
    assert should_relax_constraints(_state([], 0)) == "relax"
    assert should_relax_constraints(_state([], 1)) == "relax"
    assert should_relax_constraints(_state([], 2)) == "waitlist"
    assert should_relax_constraints(_state([], 0, prefs={"max_budget_etb": None, "preferred_sub_cities": []})) == "waitlist"
    assert should_relax_constraints(_state([], 0, intent="error")) == "end"


def test_area_names_are_normalised_deduplicated_and_unknowns_kept():
    assert normalize_sub_cities(["bole", " CMC ", "BOLE", "piassa"]) == ["Bole", "CMC", "Piassa"]
    assert normalize_sub_cities(None) == []


def test_adjacency_is_symmetric_and_unknown_areas_have_no_neighbours():
    assert "Bole" in adjacent_to("Kazanchis") and "Kazanchis" in adjacent_to("Bole")
    assert adjacent_to("Piassa") == []
    assert expand_with_adjacent(["Ayat"]) == ["Ayat", "CMC"]


def test_lead_score_grows_with_information_and_is_capped():
    empty = lead_score({}, {"max_budget_etb": None, "min_bedrooms": None, "preferred_sub_cities": []}, "small_talk", "low")
    full = lead_score({"full_name": "A", "phone": "1", "email": "e"}, PREFS, "book_tour", "high")
    assert empty == 0 and full == 100
    mid = lead_score({}, PREFS, "search", "low")
    assert 0 < mid < full


def test_money_formatting():
    assert fmt_etb(14_500_000) == "14.5M ETB" and fmt_etb(14_000_000) == "14M ETB" and fmt_etb(850_000) == "850,000 ETB"


FIRM = {"max_budget_etb": 14_000_000.0, "min_bedrooms": 3, "preferred_sub_cities": ["Debre Markos"], "area_firm": True}


def test_a_firm_area_never_widens_to_neighbours_or_any_area():
    for step in (1, 2):
        s = relaxed_scope(FIRM, step, 12.0, 2)
        assert s["effective_sub_cities"] == ["Debre Markos"]
    assert relaxed_scope(FIRM, 1, 12.0, 2)["effective_max_budget_etb"] == pytest.approx(14_000_000 * 1.12)


def test_a_firm_area_without_a_budget_goes_straight_to_the_waitlist():
    prefs = {**FIRM, "max_budget_etb": None}
    assert not can_relax(prefs)
    assert should_relax_constraints(_state([], 0, prefs=prefs)) == "waitlist"


def test_an_area_that_is_not_firm_still_widens():
    assert relaxed_scope({**FIRM, "area_firm": False}, 2, 12.0, 2)["effective_sub_cities"] == []


def test_a_firm_budget_never_stretches_but_a_first_ask_still_may():
    soft = {"max_budget_etb": 10_000_000.0, "preferred_sub_cities": ["Bole"]}
    assert relaxed_scope(soft, 2, 12.0, 2)["effective_max_budget_etb"] == pytest.approx(12_400_000)  # a small stretch, never 15M
    firm = {**soft, "budget_firm": True}
    assert relaxed_scope(firm, 1, 12.0, 2)["effective_max_budget_etb"] == 10_000_000.0


def test_with_every_limit_firm_there_is_nothing_to_relax():
    prefs = {"max_budget_etb": 10_000_000.0, "preferred_sub_cities": ["Bole"], "budget_firm": True, "area_firm": True}
    assert not can_relax(prefs)
    assert should_relax_constraints(_state([], 0, prefs=prefs)) == "waitlist"
    assert can_relax({**prefs, "budget_firm": False})
