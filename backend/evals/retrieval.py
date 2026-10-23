"""Retrieval quality: from a buyer's own words to the units they are shown.

Each case is a message and the units a sensible broker would put in front of that buyer. The whole front half of the agent runs for
real (language model reading the message, embedding, hybrid search, the widening loop), so a miss can come from any of them. The
recommendation wording is skipped: it cannot change which units are found.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean

from langchain_core.messages import HumanMessage

from app.agent.nodes import should_relax_constraints
from evals.harness import Env

DATA = Path(__file__).resolve().parent / "data" / "retrieval.jsonl"
UNITS = {
    "AYS": "Ayat Square Studios Studio", "AYH": "Ayat Heights 2BR Apartment", "AYG": "Ayat Garden Residences 3BR Townhouse",
    "AYV": "Ayat Hill Villas 4BR Villa", "GKS": "Gift Kazanchis Suites Studio", "GSP2": "Gift Summit Park 2BR Apartment",
    "GSH": "Gift Sarbet Heights 2BR Apartment", "GSP3": "Gift Summit Park 3BR Apartment", "GBC": "Gift Bole Court 3BR Apartment",
    "GLH": "Gift Lebu Homes 4BR Twin Villa", "NKP": "Noah Kazanchis Plaza 1BR Apartment", "NSG": "Noah Sarbet Gardens 3BR Apartment",
    "NB2": "Noah Bole Towers 2BR Apartment", "NB3": "Noah Bole Towers 3BR Apartment", "NPH": "Noah Bole Penthouse Penthouse 4BR",
}
THRESHOLD_SWEEP = (0.50, 0.55, 0.60, 0.64, 0.68, 0.72, 0.76, 0.80)


@dataclass
class Case:
    id: str
    lang: str
    message: str
    relevant: list[str] = field(default_factory=list)
    expect_none: bool = False
    needs_relaxation: bool = False
    hard: bool = False  # lifestyle-only, misspelt, numeric or deliberately unanswerable: separates a good system from a lucky one


@dataclass
class CaseResult:
    case: Case
    intent: str
    returned: list[str]
    relaxation_step: int
    route: str
    error: str | None = None

    @property
    def relevant_titles(self) -> list[str]:
        return [UNITS[c] for c in self.case.relevant]

    @property
    def hit(self) -> bool:
        return any(t in self.relevant_titles for t in self.returned[:3])

    @property
    def reciprocal_rank(self) -> float:
        for i, t in enumerate(self.returned[:3], start=1):
            if t in self.relevant_titles:
                return 1.0 / i
        return 0.0

    @property
    def precision(self) -> float:
        top = self.returned[:3]
        return sum(t in self.relevant_titles for t in top) / len(top) if top else 0.0

    @property
    def recall(self) -> float:
        want = self.relevant_titles
        return len({t for t in self.returned[:3] if t in want}) / min(3, len(want)) if want else 0.0


def load_cases(path: Path = DATA) -> list[Case]:
    cases = [Case(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for c in cases:
        unknown = [u for u in c.relevant if u not in UNITS]
        if unknown or (c.expect_none and c.relevant):
            raise ValueError(f"case {c.id} is mislabelled: {unknown or 'expect_none with relevant units'}")
    return cases


async def run_case(env: Env, case: Case) -> CaseResult:
    """The agent's front half: read the message, search, widen while nothing is found. Mirrors the graph's own routing."""
    buyer = await env.new_buyer(case.id)
    state: dict = {"buyer_id": str(buyer["id"]), "messages": [], "inbound": {"kind": "text", "text": case.message}, "active_matches": []}

    def merge(update: dict) -> None:
        for k, v in update.items():
            if k == "messages":
                state["messages"] = [*state["messages"], *v]
            else:
                state[k] = v

    try:
        merge(await env.nodes.intake(state))
        merge(await env.nodes.router(state))
        if state.get("intent") != "search":
            return CaseResult(case, state.get("intent", ""), [], 0, "not-a-search")
        route = ""
        for _ in range(env.settings.max_relaxation_steps + 2):
            merge(await env.nodes.discovery(state))
            route = should_relax_constraints(state)
            if route != "relax":
                break
            merge(await env.nodes.constraint_relaxer(state))
        titles = [m["title"] for m in state.get("active_matches", [])]
        return CaseResult(case, "search", titles, state["search_scope"]["relaxation_step"], route)
    except Exception as exc:  # a case that crashes is a failed case, not a failed run
        return CaseResult(case, state.get("intent", ""), [], 0, "error", error=f"{type(exc).__name__}: {exc}"[:200])


def summarize(results: list[CaseResult]) -> dict:
    wanted = [r for r in results if r.case.relevant]
    none = [r for r in results if r.case.expect_none]
    relax = [r for r in results if r.case.needs_relaxation]

    def block(rs: list[CaseResult]) -> dict:
        return {
            "cases": len(rs),
            "hit_at_3": round(mean(r.hit for r in rs), 3) if rs else None,
            "recall_at_3": round(mean(r.recall for r in rs), 3) if rs else None,
            "precision_at_3": round(mean(r.precision for r in rs), 3) if rs else None,
            "mrr": round(mean(r.reciprocal_rank for r in rs), 3) if rs else None,
        }

    return {
        "overall": block(wanted),
        "by_language": {lang: block([r for r in wanted if r.case.lang == lang]) for lang in sorted({r.case.lang for r in wanted})},
        "by_difficulty": {"everyday": block([r for r in wanted if not r.case.hard]), "hard": block([r for r in wanted if r.case.hard])},
        "relaxation": {
            "cases": len(relax),
            "recovered": round(mean(r.hit and r.relaxation_step >= 1 for r in relax), 3) if relax else None,
            "found_without_widening": sum(r.hit and r.relaxation_step == 0 for r in relax),
        },
        "no_match_expected": {
            "cases": len(none),
            "correctly_empty": round(mean(not r.returned for r in none), 3) if none else None,
            "false_positives": sum(bool(r.returned) for r in none),
        },
        "intent_errors": sum(r.intent != "search" for r in results),
        "crashed": sum(r.error is not None for r in results),
    }


async def run_all(env: Env, cases: list[Case], progress=None) -> list[CaseResult]:
    results = []
    for c in cases:
        r = await run_case(env, c)
        results.append(r)
        if progress:
            progress(f"  {c.id}: {'ok' if (r.hit if c.relevant else not r.returned) else 'MISS'}{' ERROR ' + r.error if r.error else ''}")
    return results


def misses(results: list[CaseResult]) -> list[dict]:
    out = []
    for r in results:
        if r.case.expect_none and r.returned:
            out.append({"id": r.case.id, "problem": "returned units for a request nothing matches", "returned": r.returned, "message": r.case.message})
        elif r.case.relevant and not r.hit:
            out.append({"id": r.case.id, "problem": r.error or "no relevant unit in the top 3", "returned": r.returned, "wanted": r.relevant_titles, "message": r.case.message})
    return out


def to_json(results: list[CaseResult]) -> list[dict]:
    return [{**asdict(r.case), "intent": r.intent, "returned": r.returned, "relaxation_step": r.relaxation_step, "route": r.route, "hit": r.hit, "error": r.error} for r in results]
