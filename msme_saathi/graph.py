"""LangGraph StateGraph wiring for MSME-Incentive Saathi."""

from __future__ import annotations

from typing import Any, Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from msme_saathi.agents import (
    application_draft_node,
    application_submit_node,
    approval_gate_node,
    document_checker_node,
    eligibility_analyst_node,
    intake_agent_node,
    response_synthesizer_node,
    supervisor_planner_node,
)
from msme_saathi.state import MSMEState

_ROUTES = {
    "intake_agent": "intake_agent",
    "eligibility_analyst": "eligibility_analyst",
    "document_checker": "document_checker",
    "application_draft": "application_draft",
    "approval_gate": "approval_gate",
    "application_submit": "application_submit",
    "response_synthesizer": "response_synthesizer",
}


def _route_next(state: MSMEState) -> str:
    target = state.get("next_node", "response_synthesizer")
    return target if target in _ROUTES else "response_synthesizer"


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    interrupt_before: Sequence[str] | None = None,
):
    """Build and compile the MSME-Incentive Saathi StateGraph."""
    graph = StateGraph(MSMEState)

    graph.add_node("supervisor_planner", supervisor_planner_node)
    graph.add_node("intake_agent", intake_agent_node)
    graph.add_node("eligibility_analyst", eligibility_analyst_node)
    graph.add_node("document_checker", document_checker_node)
    graph.add_node("application_draft", application_draft_node)
    graph.add_node("approval_gate", approval_gate_node)
    graph.add_node("application_submit", application_submit_node)
    graph.add_node("response_synthesizer", response_synthesizer_node)

    graph.add_edge(START, "supervisor_planner")
    graph.add_conditional_edges("supervisor_planner", _route_next, _ROUTES)

    for node in (
        "intake_agent",
        "eligibility_analyst",
        "document_checker",
        "application_draft",
        "approval_gate",
        "application_submit",
    ):
        graph.add_edge(node, "supervisor_planner")

    graph.add_edge("response_synthesizer", END)
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before) if interrupt_before else None,
    )


def export_mermaid() -> str:
    """Return a Mermaid diagram of the compiled graph."""
    return build_graph().get_graph().draw_mermaid()
