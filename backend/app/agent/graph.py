from __future__ import annotations

import time
from dataclasses import dataclass
from functools import wraps
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph

from app.agent.deps import AgentDeps
from app.agent.nodes import AgentNodes, should_relax_constraints
from app.agent.state import BrokerAgentState
from app.events import summarize_update
from app.gemini import CALL_CONTEXT

ROUTES = {"search": "discovery", "details": "details", "compare": "analyst", "book_tour": "scheduler", "book_consultation": "scheduler", "small_talk": "smalltalk", "error": END}


def route_after_intake(state: BrokerAgentState) -> str:
    return END if state.get("transcript_failed") else "router"


def route_by_intent(state: BrokerAgentState) -> str:
    return ROUTES.get(state.get("intent", ""), END)


def traced(name: str, fn: Any):
    """Tags every model request a node makes with the node's name and the buyer, for the call log."""

    @wraps(fn)
    async def run(state: BrokerAgentState) -> dict:
        token = CALL_CONTEXT.set({"node": name, "buyer_id": state.get("buyer_id")})
        try:
            return await fn(state)
        finally:
            CALL_CONTEXT.reset(token)

    return run


def build_graph(deps: AgentDeps, checkpointer: Any):
    nodes = AgentNodes(deps)
    g = StateGraph(BrokerAgentState)
    for name, fn in {
        "intake": nodes.intake, "router": nodes.router, "discovery": nodes.discovery,
        "relaxer": nodes.constraint_relaxer, "recommend": nodes.recommend, "waitlist": nodes.waitlist,
        "smalltalk": nodes.smalltalk, "details": nodes.details, "analyst": nodes.analyst, "scheduler": nodes.scheduler,
        "human_gate": nodes.human_gate,
    }.items():
        g.add_node(name, traced(name, fn))

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake, {"router": "router", END: END})
    g.add_conditional_edges("router", route_by_intent, {v: v for v in set(ROUTES.values()) if v != END} | {END: END})
    g.add_conditional_edges(
        "discovery", should_relax_constraints,
        {"relax": "relaxer", "recommend": "recommend", "waitlist": "waitlist", "end": END},
    )
    g.add_edge("relaxer", "discovery")
    g.add_edge("scheduler", "human_gate")
    for terminal in ("recommend", "waitlist", "smalltalk", "details", "analyst", "human_gate"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)


@dataclass
class TurnResult:
    reply: str | None
    state: dict


class BrokerAgent:
    def __init__(self, deps: AgentDeps, checkpointer: Any):
        self._deps = deps
        self.graph = build_graph(deps, checkpointer)

    async def add_message(self, thread_id: str, message: BaseMessage, **state_updates: Any) -> None:
        """Write a message into the conversation history without running the agent (used during human takeover
        and for messages the system sends on its own). Extra keyword arguments update other state fields, for
        example active_matches, so that "book the first one" refers to what the buyer was just shown."""
        config = {"configurable": {"thread_id": thread_id}}
        # as_node="human_gate" leads straight to END, so no node is left pending for the next real turn.
        await self.graph.aupdate_state(config, {"messages": [message], **state_updates}, as_node="human_gate")

    async def handle(
        self, *, buyer_id: str, thread_id: str, inbound: dict, chat_id: int | str | None = None
    ) -> TurnResult:
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": self._deps.settings.recursion_limit}
        bus, ident = self._deps.events, {"thread_id": thread_id, "buyer_id": buyer_id}
        started = last = time.monotonic()
        if bus:
            bus.publish("turn_started", **ident, inbound_kind=inbound.get("kind"))
        try:
            # "updates" mode reports each node as it finishes, which is what the live inspector draws.
            async for chunk in self.graph.astream({"buyer_id": buyer_id, "chat_id": chat_id, "inbound": inbound}, config, stream_mode="updates"):
                for node, update in chunk.items():
                    now = time.monotonic()
                    if bus:
                        bus.publish("node", **ident, node=node, ms=round((now - last) * 1000), **summarize_update(update if isinstance(update, dict) else None))
                    last = now
            state = (await self.graph.aget_state(config)).values
        except Exception as exc:
            if bus:
                bus.publish("turn_failed", **ident, error=type(exc).__name__)
            raise
        if bus:
            bus.publish("turn_finished", **ident, ms=round((time.monotonic() - started) * 1000))
        return TurnResult(reply=state.get("reply"), state=state)
