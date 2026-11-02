"""What each plan allows, and what an agency has used today.

The assistant is the costly part (every buyer message can cost several model calls), so the cap is on buyer messages answered per
day, counted from the agency's own chat history since midnight UTC. Over the cap the assistant stops answering with the model: the
buyer is told once that the broker will reply, the message stays in the broker's inbox, and nothing else is lost. Taking over a chat
by hand is never blocked.

Two more limits, both per plan and both counted from the agency's own rows. The assistant also has a monthly budget in money: the tokens
recorded for each model call (`model_calls`) times the price per million tokens set in `MODEL_PRICES`. Over it the assistant behaves as
over the daily cap, with its own notice. And an agency may queue only so many background jobs a day; the platform's own maintenance
jobs and the operator's agency are not counted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.access import Principal, console_access

router = APIRouter()

PLAN_DAILY_MESSAGES = {"free": 100, "pro": 1_000, "business": 10_000}
PLAN_MONTHLY_BUDGET_USD = {"free": 2.0, "pro": 25.0, "business": 250.0}
PLAN_DAILY_JOBS = {"free": 300, "pro": 3_000, "business": 30_000}
DEFAULT_PLAN = "free"


def daily_limit(plan: str | None) -> int:
    return PLAN_DAILY_MESSAGES.get(plan or DEFAULT_PLAN, PLAN_DAILY_MESSAGES[DEFAULT_PLAN])


def exceeded(usage: dict) -> bool:
    """`usage` is Repository.usage_today(). The buyer message is saved before this is asked, so the limit-plus-first message is the first one refused."""
    return usage["buyer_messages"] > daily_limit(usage["plan"])


def monthly_budget(plan: str | None) -> float:
    return PLAN_MONTHLY_BUDGET_USD.get(plan or DEFAULT_PLAN, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN])


def daily_jobs(plan: str | None) -> int:
    return PLAN_DAILY_JOBS.get(plan or DEFAULT_PLAN, PLAN_DAILY_JOBS[DEFAULT_PLAN])


def parse_prices(text: str) -> dict[str, tuple[float, float]]:
    """`name=input/output` pairs, comma separated, in US dollars per million tokens. `default` prices any model not named.
    A malformed entry is skipped rather than stopping the server, so a typo cannot take the assistant down."""
    out: dict[str, tuple[float, float]] = {}
    for part in text.replace("\n", ",").split(","):
        name, _, value = part.strip().partition("=")
        low, _, high = value.partition("/")
        try:
            if name.strip() and low.strip() and high.strip():
                out[name.strip()] = (float(low), float(high))
        except ValueError:
            continue
    return out


def spend_usd(rows: list[dict], prices: dict[str, tuple[float, float]]) -> float:
    """`rows` is Repository.model_usage_month(): tokens per model. A model with no price of its own is priced as `default`
    (and as free when there is no default either, which the usage endpoint shows as unpriced calls)."""
    total = 0.0
    for r in rows:
        low, high = prices.get(r["model"] or "", prices.get("default", (0.0, 0.0)))
        total += (int(r["input_tokens"]) * low + int(r["output_tokens"]) * high) / 1_000_000
    return total


def over_budget(plan: str | None, spent_usd: float) -> bool:
    return spent_usd >= monthly_budget(plan)


@router.get("/api/usage")
async def usage(request: Request, p: Principal = Depends(console_access)):
    repo, settings = request.app.state.repo, request.app.state.settings
    used = await repo.usage_today()
    limit = daily_limit(used["plan"])
    month = await repo.model_usage_month()
    prices = parse_prices(settings.model_prices)
    spent = spend_usd(month, prices)
    return {
        "plan": used["plan"], "daily_message_limit": limit, "buyer_messages_today": used["buyer_messages"],
        "model_calls_today": used["model_calls"], "remaining": max(0, limit - used["buyer_messages"]),
        "over_limit": exceeded(used),
        "month_spend_usd": round(spent, 4), "monthly_budget_usd": monthly_budget(used["plan"]), "over_budget": over_budget(used["plan"], spent),
        "model_calls_month": sum(int(r["calls"]) for r in month), "model_calls_without_tokens": sum(int(r["unmetered"]) for r in month),
        "prices_as_of": settings.model_prices_as_of,
        "jobs_today": await repo.jobs_today(), "daily_job_limit": daily_jobs(used["plan"]),
    }
