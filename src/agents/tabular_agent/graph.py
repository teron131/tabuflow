"""Workflow graph for the deterministic tabular analysis agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from llm_harness.tools.tabular.tools import make_tabular_tools

from .nodes import MAX_VALIDATION_ATTEMPTS, make_answer_node, make_extract_node, make_skills_node, make_sql_node, make_validate_node, save_node
from .state import TabularTaskInput, TabularTaskOutput, TabularTaskState


def route_after_extract(state: TabularTaskState) -> str:
    """Route after extraction."""
    return "sql" if state.status == "extracted" else "answer"


def route_after_sql(state: TabularTaskState) -> str:
    """Route after SQL analysis."""
    return "validate" if state.status == "complete" and bool(state.candidate_sql) else "answer"


def route_after_validate(state: TabularTaskState) -> str:
    """Route after result validation."""
    if state.status == "validated":
        return "save"
    if state.status == "needs_revision" and state.validation_attempts < MAX_VALIDATION_ATTEMPTS:
        return "sql"
    return "answer"


def create_tabular_graph(
    *,
    llm: Any,
    prompt: str = "",
    root_dir: str | Path | None = None,
) -> CompiledStateGraph:
    """Create the deterministic tabular-to-SQL graph for the billing app."""
    tabular_tools = make_tabular_tools(root_dir=root_dir)
    builder = StateGraph(
        TabularTaskState,
        input_schema=TabularTaskInput,
        output_schema=TabularTaskOutput,
    )
    builder.add_node("skills", make_skills_node())
    builder.add_node("extract", make_extract_node(tabular_tools))
    builder.add_node(
        "sql",
        make_sql_node(
            llm=llm,
            prompt=prompt,
        ),
    )
    builder.add_node("validate", make_validate_node(llm))
    builder.add_node("save", save_node)
    builder.add_node(
        "answer",
        make_answer_node(
            llm,
            prompt=prompt,
        ),
    )

    builder.add_edge(START, "skills")
    builder.add_edge("skills", "extract")
    builder.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "sql": "sql",
            "answer": "answer",
        },
    )
    builder.add_conditional_edges(
        "sql",
        route_after_sql,
        {
            "validate": "validate",
            "answer": "answer",
        },
    )
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "save": "save",
            "sql": "sql",
            "answer": "answer",
        },
    )
    builder.add_edge("save", "answer")
    builder.add_edge("answer", END)
    return builder.compile()
