from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class BrokerAgentState(TypedDict, total=False):
    """Everything is plain JSON-friendly data so any checkpointer can store it.

    Persistent across turns: messages, buyer_preferences, buyer_profile, preference_summary, deal_stage, active_matches.
    Turn-scoped (rebuilt by the router every turn): everything under "turn".
    """

    messages: Annotated[list[AnyMessage], add_messages]

    # supplied by the channel layer for each turn
    buyer_id: str
    chat_id: int | str | None
    inbound: dict  # {"kind": "text", "text": ...} or {"kind": "voice", "file_id": ..., "mime_type": ...}

    # persistent
    buyer_preferences: dict  # max_budget_etb, min_bedrooms, preferred_sub_cities. Never changed by search or relaxation.
    buyer_profile: dict  # full_name, phone, email
    preference_summary: str
    deal_stage: str
    active_matches: list[dict]  # options shown most recently, in the order the buyer saw them
    pending_booking: dict | None  # {"ids": [...]} while a time is being chosen, so a tap on a slot knows which options it is for

    language: str  # en or am: the language of the buyer's latest message
    conversation_summary: str  # the older part of a long conversation, in a few sentences
    monthly_budget_etb: float | None

    # turn
    injection: bool  # the message tried to give the agent instructions
    handoff_reason: str | None  # asked or frustrated: the broker should take over
    objections_now: list[str]
    intent: str
    urgency: str
    query_text: str
    query_embedding: list[float] | None
    ranking: str | None  # cheapest, priciest, highest_yield or largest: search by that rule instead of by similarity
    search_scope: dict  # effective_max_budget_etb, effective_sub_cities, relaxation_step, max_relaxation_steps
    selected_property_ids: list[str]
    tour_start_iso: str | None
    comparison_matrix: dict | None
    booked: bool
    lead_reg_failed: list[str]
    transcript_failed: bool
    human_takeover: bool
    reply: str | None
    quick_replies: list[list[dict]]  # rows of {label, text}: buttons the channel may show under the reply
    attachments: list[dict]  # files for a channel without Telegram to show with the reply: {"kind": "model3d", "property_id", "title"} or {"kind": "comparison", "tour_id"}


EMPTY_PREFS = {"max_budget_etb": None, "min_bedrooms": None, "preferred_sub_cities": [], "area_firm": False, "budget_firm": False, "widened_last": False}
