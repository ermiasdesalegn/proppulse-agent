from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph

from app.agent.deps import AgentDeps
from app.agent.nodes import AgentNodes, should_relax_constraints
from app.agent.state import BrokerAgentState

ROUTES = {"search": "discovery", "compare": "analyst", "book_tour": "scheduler", "small_talk": "smalltalk", "error": END}


def route_after_intake(state: BrokerAgentState) -> str:
    return END if state.get("transcript_failed") else "router"


def route_by_intent(state: BrokerAgentState) -> str:
    return ROUTES.get(state.get("intent", ""), END)


def build_graph(deps: AgentDeps, checkpointer: Any):
    nodes = AgentNodes(deps)
    g = StateGraph(BrokerAgentState)
    for name, fn in {
        "intake": nodes.intake, "router": nodes.router, "discovery": nodes.discovery,
        "relaxer": nodes.constraint_relaxer, "recommend": nodes.recommend, "waitlist": nodes.waitlist,
        "smalltalk": nodes.smalltalk, "analyst": nodes.analyst, "scheduler": nodes.scheduler,
        "human_gate": nodes.human_gate,
    }.items():
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake, {"router": "router", END: END})
    g.add_conditional_edges("router", route_by_intent, {v: v for v in set(ROUTES.values()) if v != END} | {END: END})
    g.add_conditional_edges(
        "discovery", should_relax_constraints,
        {"relax": "relaxer", "recommend": "recommend", "waitlist": "waitlist", "end": END},
    )
    g.add_edge("relaxer", "discovery")
    g.add_edge("scheduler", "human_gate")
    for terminal in ("recommend", "waitlist", "smalltalk", "analyst", "human_gate"):
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

    async def add_message(self, thread_id: str, message: BaseMessage) -> None:
        """Write a message into the conversation history without running the agent (used during human takeover)."""
        config = {"configurable": {"thread_id": thread_id}}
        # as_node="human_gate" leads straight to END, so no node is left pending for the next real turn.
        await self.graph.aupdate_state(config, {"messages": [message]}, as_node="human_gate")

    async def handle(
        self, *, buyer_id: str, thread_id: str, inbound: dict, chat_id: int | str | None = None
    ) -> TurnResult:
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": self._deps.settings.recursion_limit}
        state = await self.graph.ainvoke({"buyer_id": buyer_id, "chat_id": chat_id, "inbound": inbound}, config)
        return TurnResult(reply=state.get("reply"), state=state)
