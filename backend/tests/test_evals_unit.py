"""The evaluation machinery itself: dataset sanity, metric arithmetic, report checks. (The replay regression is in test_evals_replay.py.)"""
import json
from collections import Counter

import pytest

from evals import report, retrieval
from evals.retrieval import UNITS, Case, CaseResult, load_cases, misses, summarize
from evals.scenarios import SCENARIOS


def test_the_dataset_is_well_formed():
    cases = load_cases()
    assert len(cases) >= 55 and len({c.id for c in cases}) == len(cases), "unique ids"
    assert Counter(c.lang for c in cases).keys() == {"en", "am", "mix"}
    assert all(c.expect_none != bool(c.relevant) for c in cases), "every case is either 'finds these' or 'finds nothing'"
    assert all(c.relevant == list(dict.fromkeys(c.relevant)) for c in cases), "no duplicate labels"
    assert all(set(c.relevant) <= set(UNITS) for c in cases)
    assert sum(c.expect_none for c in cases) >= 5 and sum(c.needs_relaxation for c in cases) >= 3 and sum(c.hard for c in cases) >= 12


def test_every_labelled_unit_exists_in_the_demo_catalogue():
    import psycopg
    from tests.localdb import local_database_url

    with psycopg.connect(local_database_url()) as conn:
        titles = {r[0] for r in conn.execute("select title from properties")}
    assert set(UNITS.values()) <= titles


def test_a_mislabelled_case_is_refused(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "x", "lang": "en", "message": "m", "relevant": ["NOPE"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="mislabelled"):
        load_cases(bad)
    bad.write_text(json.dumps({"id": "x", "lang": "en", "message": "m", "relevant": ["NB2"], "expect_none": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="mislabelled"):
        load_cases(bad)


def result(relevant, returned, *, lang="en", step=0, **kw):
    return CaseResult(Case("c", lang, "m", relevant=relevant, **kw), "search", [UNITS[r] for r in returned], step, "recommend")


def test_ranking_metrics():
    r = result(["NB2", "NB3"], ["GBC", "NB3", "AYS"])
    assert r.hit and r.reciprocal_rank == 0.5 and r.precision == pytest.approx(1 / 3) and r.recall == pytest.approx(0.5)
    first = result(["NB2"], ["NB2", "GBC"])
    assert first.reciprocal_rank == 1.0 and first.recall == 1.0 and first.precision == 0.5
    miss = result(["NB2"], ["GBC", "AYS", "GKS"])
    assert not miss.hit and miss.reciprocal_rank == 0 and miss.precision == 0
    assert result(["NB2"], []).precision == 0, "returning nothing is not a division error"


def test_only_the_top_three_count():
    r = result(["NB2"], ["GBC", "AYS", "GKS", "NB2"])
    assert not r.hit and r.reciprocal_rank == 0


def test_a_summary_separates_languages_empty_cases_and_widening():
    results = [
        result(["NB2"], ["NB2"], lang="en"), result(["NB3"], [], lang="am"), result(["AYG"], ["AYG"], lang="en", needs_relaxation=True, step=1),
        result([], [], expect_none=True), result([], ["AYS"], expect_none=True), result(["NB2"], ["NB2"], lang="mix", needs_relaxation=True, step=0),
    ]
    s = summarize(results)
    assert s["overall"]["cases"] == 4 and s["overall"]["hit_at_3"] == 0.75
    assert s["by_language"]["en"]["hit_at_3"] == 1.0 and s["by_language"]["am"]["hit_at_3"] == 0.0 and set(s["by_language"]) == {"en", "am", "mix"}
    assert s["no_match_expected"] == {"cases": 2, "correctly_empty": 0.5, "false_positives": 1}
    assert s["relaxation"] == {"cases": 2, "recovered": 0.5, "found_without_widening": 1}
    assert [m["id"] for m in misses(results)] == ["c", "c"]


def test_a_crashed_case_is_counted_and_reported_not_hidden():
    crashed = CaseResult(Case("boom", "en", "m", relevant=["NB2"]), "", [], 0, "error", error="ValueError: x")
    s = summarize([crashed])
    assert s["crashed"] == 1 and misses([crashed])[0]["problem"] == "ValueError: x"


# ---------------- thresholds ----------------

def make_results(hit=0.9, mrr=0.8, empty=1.0, relax=1.0, passed=20, ungrounded=0, failing=()):
    items = [{"name": "ok", "passed": True, "known_gap": None, "checks": []}] + [{"name": n, "passed": False, "known_gap": None, "checks": []} for n in failing]
    return {
        "mode": "replay", "ran_at": "2026-11-02T00:00:00+00:00",
        "retrieval": {"summary": {"overall": {"hit_at_3": hit, "mrr": mrr, "cases": 1, "recall_at_3": 1, "precision_at_3": 1}, "by_language": {},
                                  "no_match_expected": {"correctly_empty": empty, "cases": 1, "false_positives": 0},
                                  "relaxation": {"recovered": relax, "cases": 1, "found_without_widening": 0}, "intent_errors": 0, "crashed": 0},
                      "misses": [], "live_calls": 0, "threshold_sweep": []},
        "scenarios": {"items": items, "passed": passed, "counted": 22, "known_gaps": 1, "gaps_closed": 0, "turns": 30, "grounded_turns": 30 - ungrounded, "ungrounded": []},
    }


@pytest.fixture
def thresholds(tmp_path, monkeypatch):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"retrieval": {"hit_at_3": 0.85, "mrr": 0.7, "correctly_empty": 1.0, "relaxation_recovered": 0.9},
                                "scenarios": {"min_passed": 20, "max_ungrounded_turns": 1}}), encoding="utf-8")
    monkeypatch.setattr(report, "THRESHOLDS", path)


def test_results_at_or_above_the_thresholds_pass(thresholds):
    assert report.check(make_results()) == 0
    assert report.check(make_results(hit=0.85, mrr=0.7, ungrounded=1)) == 0, "equal is not a regression"


@pytest.mark.parametrize("override", [{"hit": 0.8}, {"mrr": 0.6}, {"empty": 0.9}, {"relax": 0.5}, {"passed": 19}, {"ungrounded": 2}, {"failing": ["new_bug"]}])
def test_anything_worse_fails_the_check(thresholds, override, capsys):
    assert report.check(make_results(**override)) == 1
    assert "REGRESSION" in capsys.readouterr().out


def test_a_known_gap_failing_is_not_a_regression(thresholds):
    results = make_results()
    results["scenarios"]["items"].append({"name": "gap", "passed": False, "known_gap": "later", "checks": []})
    assert report.check(results) == 0


def test_the_report_and_console_summary_render(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT", tmp_path / "REPORT.md")
    results = make_results()
    results["retrieval"]["summary"]["by_language"] = {"en": results["retrieval"]["summary"]["overall"]}
    results["retrieval"]["summary"]["by_difficulty"] = {"everyday": results["retrieval"]["summary"]["overall"], "hard": results["retrieval"]["summary"]["overall"]}
    results["retrieval"]["threshold_sweep"] = [{"threshold": 0.64, "hit_at_3": 0.9, "precision_at_3": 0.8, "false_positives": 0, "correctly_empty": 1.0}]
    results["scenarios"]["items"] = [{"name": "greeting", "passed": False, "known_gap": "Phase 14", "checks": [{"name": "replies in Amharic", "ok": False, "detail": "0%"}]}]
    report.write(results)
    text = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "## Retrieval" in text and "**(used)**" in text and "known gap" in text and "replies in Amharic" in text and "| hard |" in text
    assert "hit@3 90%" in report.console(results)


# ---------------- scenarios ----------------

def test_scenarios_are_uniquely_named_and_every_known_gap_says_why():
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names)) and len(names) >= 25
    assert all(s.known_gap is None or len(s.known_gap) > 20 for s in SCENARIOS)
    assert {"affordability_estimate", "handoff_on_request", "reply_in_amharic"} <= set(names)
    assert {"search_english", "booking_full", "takeover_pauses_the_agent", "prompt_injection", "price_drop_reaches_the_right_buyer"} <= set(names)
