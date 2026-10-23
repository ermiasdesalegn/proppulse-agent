"""Payment schedules and developer-reliability ranking. Pure functions: no database, no clock of their own.

A developer sells a unit as: a down payment on signing, equal instalments while it is built, and a final part at handover.
A plan is stored as small JSON, on the unit if it differs from the developer's usual terms:

    {"installments": 8, "interval_months": 3, "handover_pct": 10}

Anything missing falls back to the developer's plan, then to a generic one, so every unit can be compared.
"""
from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

GENERIC_PLAN = {"installments": 8, "interval_months": 3, "handover_pct": 10.0}
SIGNING_DELAY_DAYS = 7  # contract signing is assumed a week after the visit
RELIABILITY_WEIGHT = 0.05  # a developer's reputation can move a result by at most this much of its 0-1 similarity score


@dataclass(frozen=True)
class Payment:
    label: str
    due: date
    pct: float
    amount_etb: float

    def to_json(self) -> dict:
        return {"label": self.label, "due": self.due.isoformat(), "pct": round(self.pct, 2), "amount_etb": round(self.amount_etb)}


def fmt_etb(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M ETB"
    return f"{value:,.0f} ETB"


def payment_schedule(p: dict, today: date) -> list[Payment]:
    """The payments for one unit (a property row as the repository returns it), from its own terms, its developer's, or the generic ones."""
    plan = resolve_plan(p.get("payment_plan"), p.get("developer_payment_plan"))
    down = p.get("down_payment_pct")
    return build_schedule(float(p["price_etb"]), None if down is None else float(down), plan,
                          signing=today + timedelta(days=SIGNING_DELAY_DAYS), delivery=p.get("delivery_date"))


def add_months(d: date, months: int) -> date:
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def resolve_plan(unit_plan: dict | None, developer_plan: dict | None) -> dict:
    """The unit's own terms, else the developer's, else the generic ones. Each field falls back on its own."""
    plan = dict(GENERIC_PLAN)
    for source in (developer_plan, unit_plan):
        for key in GENERIC_PLAN:
            value = (source or {}).get(key)
            if value is not None:
                plan[key] = value
    plan["installments"] = max(0, int(plan["installments"]))
    plan["interval_months"] = max(1, int(plan["interval_months"]))
    plan["handover_pct"] = min(max(float(plan["handover_pct"]), 0.0), 90.0)
    return plan


def build_schedule(price_etb: float, down_payment_pct: float | None, plan: dict, *, signing: date, delivery: date | None) -> list[Payment]:
    """Every payment from signing to handover, summing to exactly the price.

    Instalments are equal and `interval_months` apart; if the unit is delivered before they would all fall due, they are
    compressed to fit between signing and delivery rather than being scheduled after handover."""
    price = float(price_etb)
    down = min(max(float(down_payment_pct if down_payment_pct is not None else 20.0), 0.0), 100.0)
    handover = min(plan["handover_pct"], 100.0 - down)
    middle = 100.0 - down - handover
    n = plan["installments"] if middle > 0 else 0
    handover_date = delivery if delivery and delivery > signing else add_months(signing, plan["interval_months"] * (n + 1))

    payments = [Payment("Down payment on signing", signing, down, price * down / 100)]
    if n:
        months_available = max((handover_date.year - signing.year) * 12 + handover_date.month - signing.month, 1)
        interval = min(plan["interval_months"], max(months_available // n, 1))
        each = middle / n
        for i in range(1, n + 1):
            due = min(add_months(signing, interval * i), handover_date - timedelta(days=1)) if handover > 0 else add_months(signing, interval * i)
            payments.append(Payment(f"Instalment {i} of {n}", max(due, signing), each, price * each / 100))
    final_pct = handover if n else 100.0 - down
    if (handover > 0 or not n) and final_pct > 0:
        payments.append(Payment("On handover", handover_date, final_pct, price * final_pct / 100))
    drift = price - sum(p.amount_etb for p in payments)  # rounding is kept out of the totals
    if abs(drift) > 0.005:
        last = payments[-1]
        payments[-1] = Payment(last.label, last.due, last.pct, last.amount_etb + drift)
    return payments


def summarize(payments: Sequence[Payment]) -> dict[str, Any]:
    """The few numbers a buyer compares: what is paid at signing, how much per month on average, what is left for handover."""
    first = payments[0]
    builds = [p for p in payments[1:] if p.label.startswith("Instalment")]
    handover = next((p for p in payments if p.label == "On handover"), None)
    span_months = max((payments[-1].due.year - first.due.year) * 12 + payments[-1].due.month - first.due.month, 1)
    after_signing = sum(p.amount_etb for p in payments[1:])
    return {
        "down_payment_etb": round(first.amount_etb),
        "installment_count": len(builds),
        "installment_etb": round(builds[0].amount_etb) if builds else 0,
        "handover_etb": round(handover.amount_etb) if handover else 0,
        "handover_date": handover.due.isoformat() if handover else None,
        "months_to_handover": span_months,
        "monthly_equivalent_etb": round(after_signing / span_months),
    }


def describe(summary: dict, fmt: Callable[[float], str]) -> str:
    """One line for chat, using the caller's money formatter."""
    parts = [f"{fmt(summary['down_payment_etb'])} on signing"]
    if summary["installment_count"]:
        parts.append(f"{summary['installment_count']} payments of {fmt(summary['installment_etb'])}")
    if summary["handover_etb"]:
        parts.append(f"{fmt(summary['handover_etb'])} at handover")
    return ", then ".join([parts[0], " and ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def rank_by_reliability(matches: Sequence[Any], reliability: dict[str, float], *, weight: float = RELIABILITY_WEIGHT) -> list[Any]:
    """Order search results by similarity plus a small reward for a reliable developer (reliability is 0 to 1, unknown counts as 0.5).

    The reward is capped at `weight`, so it settles near-ties in favour of the developer who delivers, but a match that is clearly
    better (by more than `weight`) always stays ahead whatever the reputations are."""
    def score(m: Any) -> float:
        return m.similarity + weight * reliability.get(str(m.developer_id), 0.5)

    return sorted(matches, key=score, reverse=True)


def monthly_payment(principal: float, annual_rate_pct: float, years: int) -> float:
    """The level monthly payment on a loan (annuity formula)."""
    n = max(int(years) * 12, 1)
    r = annual_rate_pct / 1200.0
    return principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)


def affordability(price_etb: float, summary: dict, monthly_budget_etb: float, *, loan_share_pct: float, rate_pct: float, years: int) -> dict:
    """How a unit sits against what the buyer can pay each month: during construction (the developer's plan) and, after handover, as a bank loan."""
    during = float(summary["monthly_equivalent_etb"])
    loan = monthly_payment(float(price_etb) * loan_share_pct / 100.0, rate_pct, years)
    return {"monthly_during_construction_etb": round(during), "fits_during_construction": during <= monthly_budget_etb,
            "loan_monthly_etb": round(loan), "loan_fits": loan <= monthly_budget_etb}
