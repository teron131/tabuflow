"""LangGraph workflow construction for the file fixer agent."""

from __future__ import annotations

import functools
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .nodes import fix_node, review_node
from .nodes.runtime import coerce_state
from .state import FixerInput, FixerOutput, FixerState

type FixerRoute = Literal["fix", "review", "end"]


def route_after_fix(state: FixerState | dict) -> FixerRoute:
    """Route from the fix step to review, another pass, or the end."""
    state = coerce_state(state)
    if state.review_kind:
        return "review"
    if state.fixer_last_text == "max_turns":
        return "end"
    return "fix"


def route_after_review(state: FixerState | dict) -> FixerRoute:
    """Route from the review step back to fixing or to the end."""
    state = coerce_state(state)
    if state.fixer_completed or state.fixer_last_text in {"stalled", "max_turns"}:
        return "end"
    return "fix"


@functools.lru_cache(maxsize=1)
def create_fixer_graph() -> CompiledStateGraph:
    """Build and cache the two-step fixer workflow graph."""
    graph = StateGraph(
        FixerState,
        input_schema=FixerInput,
        output_schema=FixerOutput,
    )

    graph.add_node("fix_text", fix_node)
    graph.add_node("review_text", review_node)
    graph.add_edge(START, "review_text")
    graph.add_conditional_edges(
        "fix_text",
        route_after_fix,
        {
            "end": END,
            "fix": "fix_text",
            "review": "review_text",
        },
    )
    graph.add_conditional_edges(
        "review_text",
        route_after_review,
        {
            "end": END,
            "fix": "fix_text",
        },
    )

    return graph.compile()
