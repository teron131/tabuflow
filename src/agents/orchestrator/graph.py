"""LangGraph wiring for the staged data-analysis orchestrator."""

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..prep_agent import PrepAgent
from ..sql_agent import SQLAgent
from ..validation_agent import ValidationAgent
from .nodes import OrchestratorNodes, route_after_prep, route_after_sql, route_after_validation
from .state import OrchestratorInput, OrchestratorOutput, OrchestratorState


def build_orchestrator_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_agent: PrepAgent | None = None,
    sql_agent: SQLAgent | None = None,
    validation_agent: ValidationAgent | None = None,
    name: str = "orchestrator",
) -> CompiledStateGraph:
    """Build the parent orchestrator graph that bridges worker subgraph stages."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_agent=prep_agent,
        sql_agent=sql_agent,
        validation_agent=validation_agent,
    )

    builder = StateGraph(
        OrchestratorState,
        input_schema=OrchestratorInput,
        output_schema=OrchestratorOutput,
    )
    builder.add_node("skill_context", nodes.skill_context)
    builder.add_node("prep", nodes.prep_subgraph())
    builder.add_node("sql", nodes.sql_subgraph())
    builder.add_node("validate", nodes.validate)
    builder.add_node("save", nodes.save)
    builder.add_node("finalize", nodes.finalize)
    builder.add_edge(START, "skill_context")
    builder.add_edge("skill_context", "prep")
    builder.add_conditional_edges(
        "prep",
        route_after_prep,
        {
            "sql": "sql",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "sql",
        route_after_sql,
        {
            "validate": "validate",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "save": "save",
            "sql": "sql",
            "finalize": "finalize",
        },
    )
    builder.add_edge("save", END)
    builder.add_edge("finalize", END)
    return builder.compile(name=name)
