from __future__ import annotations

from typing import TypedDict

from .service import QueryService


class QueryGraphState(TypedDict, total=False):
    question: str
    run_id: str
    result: dict
    node_trace: list[str]


def build_query_graph(db, *, db_path: str | None = None, checkpointer=None):
    """Optional LangGraph shell for persistence/runtime interoperability.

    The bounded Evidence Loop itself remains in BusinessCodeQueryAgent so the
    same deterministic behavior is available without optional dependencies.
    This graph makes the public stages explicit and checkpoint-visible; the
    EXECUTE_EVIDENCE_LOOP node invokes that single bounded agent, not subagents.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install the 'langgraph' optional dependency") from exc

    service = QueryService(db, db_path=db_path)

    def mark(name):
        def node(state):
            return {"node_trace": [*state.get("node_trace", []), name]}
        return node

    def execute(state):
        result = service.query(state["question"])
        # LangGraph checkpoints retain the structured answer and Evidence
        # metadata only; raw evidence content remains in the direct API result.
        safe = {key: value for key, value in result.items() if key != "evidence"}
        return {"run_id": result["runId"], "result": safe, "node_trace": [*state.get("node_trace", []), "EXECUTE_EVIDENCE_LOOP"]}

    builder = StateGraph(QueryGraphState)
    stages = ["UNDERSTAND", "INITIAL_SEARCH", "LOAD_SUMMARY", "EVALUATE", "PLAN_EXPANSION", "EXPAND_EVIDENCE", "READ_RAW_EVIDENCE"]
    for stage in stages:
        builder.add_node(stage, mark(stage))
    builder.add_node("EXECUTE_EVIDENCE_LOOP", execute)
    builder.add_node("BUILD_ANSWER", mark("BUILD_ANSWER"))
    builder.add_edge(START, stages[0])
    for left, right in zip(stages, stages[1:]):
        builder.add_edge(left, right)
    builder.add_edge(stages[-1], "EXECUTE_EVIDENCE_LOOP")
    builder.add_edge("EXECUTE_EVIDENCE_LOOP", "BUILD_ANSWER")
    builder.add_edge("BUILD_ANSWER", END)
    return builder.compile(checkpointer=checkpointer)
