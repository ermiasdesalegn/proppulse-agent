"""Turns evaluation results into a readable report and a pass/fail check against evals/thresholds.json."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
THRESHOLDS = HERE / "thresholds.json"
REPORT = HERE / "REPORT.md"


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def console(results: dict) -> str:
    lines = []
    if r := results.get("retrieval"):
        s = r["summary"]
        lines.append(f"retrieval: hit@3 {_pct(s['overall']['hit_at_3'])}, MRR {s['overall']['mrr']}, precision@3 {_pct(s['overall']['precision_at_3'])}, "
                     f"empty-correct {_pct(s['no_match_expected']['correctly_empty'])}, relaxation recovered {_pct(s['relaxation']['recovered'])}, "
                     f"live model calls this run: {r['live_calls']}")
        for m in r["misses"]:
            lines.append(f"  miss {m['id']}: {m['problem']} -> {m['returned']}")
    if sc := results.get("scenarios"):
        lines.append(f"scenarios: {sc['passed']}/{sc['counted']} pass, {sc['known_gaps']} known gaps ({sc['gaps_closed']} closed), grounded turns {sc['grounded_turns']}/{sc['turns']}")
        for s in sc["items"]:
            if not s["passed"] and not s["known_gap"]:
                lines.append(f"  FAIL {s['name']}: " + "; ".join(c["name"] for c in s["checks"] if not c["ok"]))
    return "\n".join(lines)


def _load_thresholds() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.exists() else {}


def check(results: dict) -> int:
    """0 if nothing got worse than the committed thresholds, 1 otherwise."""
    t, problems = _load_thresholds(), []
    if r := results.get("retrieval"):
        s = r["summary"]
        for key, got in (("hit_at_3", s["overall"]["hit_at_3"]), ("mrr", s["overall"]["mrr"]), ("correctly_empty", s["no_match_expected"]["correctly_empty"]),
                         ("relaxation_recovered", s["relaxation"]["recovered"])):
            if key in t.get("retrieval", {}) and got is not None and got + 1e-9 < t["retrieval"][key]:
                problems.append(f"retrieval {key} {got} is below {t['retrieval'][key]}")
        if s["crashed"]:
            problems.append(f"{s['crashed']} retrieval case(s) crashed")
    if sc := results.get("scenarios"):
        floor = t.get("scenarios", {}).get("min_passed")
        if floor is not None and sc["passed"] < floor:
            problems.append(f"only {sc['passed']} scenarios pass; the floor is {floor}")
        bad = [s["name"] for s in sc["items"] if not s["passed"] and not s["known_gap"]]
        if bad:
            problems.append("scenarios failing that are not known gaps: " + ", ".join(bad))
        gaps = t.get("scenarios", {}).get("max_ungrounded_turns")
        if gaps is not None and sc["turns"] - sc["grounded_turns"] > gaps:
            problems.append(f"{sc['turns'] - sc['grounded_turns']} replies state numbers that are not in the facts (allowed: {gaps})")
    for p in problems:
        print("REGRESSION:", p)
    return 1 if problems else 0


def write(results: dict) -> None:
    out = ["# Evaluation report", "", f"Run on {results['ran_at']} in `{results['mode']}` mode. Everything runs against the demo catalogue (15 units, 3 developers) with the model's recorded answers, "
           "so a replay run is repeatable and free; `--mode record` refreshes the recording from the live model.", ""]
    if r := results.get("retrieval"):
        s = r["summary"]
        o = s["overall"]
        out += ["## Retrieval", "",
                f"{o['cases']} buyer messages with labelled correct units, in English, Amharic and mixed. The agent's real front half runs: the model reads the message, "
                "the query is embedded, hybrid search runs, and the search widens (budget +12% then +24%, neighbouring areas) while nothing is found.", "",
                "| Metric | All | " + " | ".join(s["by_language"]) + " |", "|---|---|" + "---|" * len(s["by_language"])]
        for key, label in (("hit_at_3", "Hit@3 (a correct unit in the top 3)"), ("recall_at_3", "Recall@3"), ("precision_at_3", "Precision@3"), ("mrr", "MRR")):
            fmt = (lambda v: "-" if v is None else f"{v:.2f}") if key == "mrr" else _pct
            out.append(f"| {label} | {fmt(o[key])} | " + " | ".join(fmt(b[key]) for b in s["by_language"].values()) + " |")
        d = s["by_difficulty"]
        out += ["", "| Difficulty | Cases | Hit@3 | Precision@3 | MRR |", "|---|---|---|---|---|"]
        out += [f"| {name} | {b['cases']} | {_pct(b['hit_at_3'])} | {_pct(b['precision_at_3'])} | {'-' if b['mrr'] is None else format(b['mrr'], '.2f')} |" for name, b in d.items()]
        out += ["", "| Case | Result |", "|---|---|",
                f"| Requests nothing matches ({s['no_match_expected']['cases']}) | correctly empty {_pct(s['no_match_expected']['correctly_empty'])}, false positives {s['no_match_expected']['false_positives']} |",
                f"| Needs the widening loop ({s['relaxation']['cases']}) | recovered {_pct(s['relaxation']['recovered'])} |",
                f"| Messages not read as a search | {s['intent_errors']} |", ""]
        out += ["### Why the similarity threshold is 0.64", "",
                "The same cases re-run with different thresholds. Too low and requests nothing matches return units anyway (false positives); too high and real requests find nothing.", "",
                "| Threshold | Hit@3 | Precision@3 | False positives | Correctly empty |", "|---|---|---|---|---|"]
        for row in r["threshold_sweep"]:
            mark = " **(used)**" if abs(row["threshold"] - 0.64) < 1e-9 else ""
            out.append(f"| {row['threshold']:.2f}{mark} | {_pct(row['hit_at_3'])} | {_pct(row['precision_at_3'])} | {row['false_positives']} | {_pct(row['correctly_empty'])} |")
        out += [""]
        if r["misses"]:
            out += ["### Misses", "", "| Case | Message | Problem | Returned |", "|---|---|---|---|"]
            out += [f"| {m['id']} | {m['message']} | {m['problem']} | {', '.join(m['returned']) or '-'} |" for m in r["misses"]]
            out += [""]
    if sc := results.get("scenarios"):
        out += ["## Conversations", "",
                f"{sc['counted']} scripted conversations through the real graph with a rolled-back database and mock calendar, email and Telegram: **{sc['passed']} pass**. "
                f"{sc['known_gaps']} more {'is a known gap' if sc['known_gaps'] == 1 else 'are known gaps'} (failing today for a stated reason, to be closed in a later phase; {sc['gaps_closed']} already pass). "
                f"Replies were checked for invented numbers: **{sc['grounded_turns']} of {sc['turns']}** replies state only figures present in the facts or the buyer's own words.", "",
                "| Scenario | Checks | Result |", "|---|---|---|"]
        for s in sc["items"]:
            ok = sum(c["ok"] for c in s["checks"])
            status = "pass" if s["passed"] else ("known gap" if s["known_gap"] else "**FAIL**")
            out.append(f"| {s['name']} | {ok}/{len(s['checks'])} | {status} |")
        failing = [s for s in sc["items"] if not s["passed"]]
        if failing:
            out += ["", "### Not passing", ""]
            for s in failing:
                out.append(f"- **{s['name']}**{' (known gap: ' + s['known_gap'] + ')' if s['known_gap'] else ''}: " + "; ".join(f"{c['name']}" + (f" ({c['detail']})" if c.get("detail") else "") for c in s["checks"] if not c["ok"]))
        if sc["ungrounded"]:
            out += ["", "### Figures not found in the facts", ""]
            out += [f"- {u['scenario']}: `{u['claim']}` in \"{u['reply']}\"" for u in sc["ungrounded"]]
        out += [""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
