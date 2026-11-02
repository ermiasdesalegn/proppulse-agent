"""What each plan allows, and what an agency has used today.

The assistant is the costly part (every buyer message can cost several model calls), so the cap is on buyer messages answered per
day, counted from the agency's own chat history since midnight UTC. Over the cap the assistant stops answering with the model: the
buyer is told once that the broker will reply, the message stays in the broker's inbox, and nothing else is lost. Taking over a chat
by hand is never blocked.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.access import Principal, console_access

router = APIRouter()

PLAN_DAILY_MESSAGES = {"free": 100, "pro": 1_000, "business": 10_000}
DEFAULT_PLAN = "free"


def daily_limit(plan: str | None) -> int:
    return PLAN_DAILY_MESSAGES.get(plan or DEFAULT_PLAN, PLAN_DAILY_MESSAGES[DEFAULT_PLAN])


def exceeded(usage: dict) -> bool:
    """`usage` is Repository.usage_today(). The buyer message is saved before this is asked, so the limit-plus-first message is the first one refused."""
    return usage["buyer_messages"] > daily_limit(usage["plan"])


@router.get("/api/usage")
async def usage(request: Request, p: Principal = Depends(console_access)):
    used = await request.app.state.repo.usage_today()
    limit = daily_limit(used["plan"])
    return {
        "plan": used["plan"], "daily_message_limit": limit, "buyer_messages_today": used["buyer_messages"],
        "model_calls_today": used["model_calls"], "remaining": max(0, limit - used["buyer_messages"]),
        "over_limit": exceeded(used),
    }
