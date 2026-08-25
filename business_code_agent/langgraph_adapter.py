from __future__ import annotations

from typing import TypedDict

from .models import AgentState
from .orchestrator import Orchestrator


class GraphState(TypedDict, total=False):
    question: str
    agent_state: dict
    result: dict


def build_graph(db, checkpointer=None):
    """Build a checkpointed node-per-iteration Evidence Loop.

    Each checkpoint contains only structured facts and Evidence IDs. Raw source,
    requirement text and the rendered answer remain outside durable graph state.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install the 'langgraph' optional dependency") from exc

    orchestrator = Orchestrator(db)

    def understand(state: GraphState) -> GraphState:
        agent_state = orchestrator.understand(state["question"])
        return {"agent_state": agent_state.to_reference_dict()}

    def evidence_step(state: GraphState) -> GraphState:
        agent_state = AgentState(**state["agent_state"])
        orchestrator.advance(agent_state)
        return {"agent_state": agent_state.to_reference_dict()}

    def route(state: GraphState) -> str:
        agent_state = state["agent_state"]
        if agent_state["evidence_status"] == "SUFFICIENT" or agent_state["iteration"] >= orchestrator.max_iterations:
            return "persist"
        return "evidence_step"

    def persist(state: GraphState) -> GraphState:
        agent_state = AgentState(**state["agent_state"])
        orchestrator.finalize(agent_state)
        return {"result": agent_state.to_reference_dict()}

    builder = StateGraph(GraphState)
    builder.add_node("understand", understand)
    builder.add_node("evidence_step", evidence_step)
    builder.add_node("persist", persist)
    builder.add_edge(START, "understand")
    builder.add_edge("understand", "evidence_step")
    builder.add_conditional_edges("evidence_step", route, {
        "evidence_step": "evidence_step",
        "persist": "persist",
    })
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)
