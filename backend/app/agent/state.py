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

    # turn
    intent: str
    urgency: str
    query_text: str
    query_embedding: list[float] | None
    search_scope: dict  # effective_max_budget_etb, effective_sub_cities, relaxation_step, max_relaxation_steps
    selected_property_ids: list[str]
    tour_start_iso: str | None
    comparison_matrix: dict | None
    booked: bool
    lead_reg_failed: list[str]
    transcript_failed: bool
    human_takeover: bool
    reply: str | None


EMPTY_PREFS = {"max_budget_etb": None, "min_bedrooms": None, "preferred_sub_cities": []}
