"""LangGraph wiring for the staged data-analysis orchestrator."""

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..prep_agent import PrepAgent
from ..validation_agent import ValidationAgent
from .nodes import OrchestratorNodes, route_after_prep_stage
from .sql_stage import DraftFn, RuntimeRepairFn
from .state import OrchestratorInput, OrchestratorOutput, OrchestratorState


def build_orchestrator_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_agent: PrepAgent | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_agent: ValidationAgent | None = None,
    name: str = "orchestrator",
) -> CompiledStateGraph:
    """Build the parent orchestrator graph that owns the workflow stages."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_agent=prep_agent,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_agent=validation_agent,
    )

    builder = StateGraph(
        OrchestratorState,
        input_schema=OrchestratorInput,
        output_schema=OrchestratorOutput,
    )
    builder.add_node("skill_context", nodes.skill_context)
    builder.add_node("prep_stage", nodes.prep_stage_graph())
    builder.add_node("query_stage", nodes.query_stage_graph())
    builder.add_node("answer", nodes.answer)
    builder.add_edge(START, "skill_context")
    builder.add_edge("skill_context", "prep_stage")
    builder.add_conditional_edges(
        "prep_stage",
        route_after_prep_stage,
        {
            "query_stage": "query_stage",
            "answer": "answer",
        },
    )
    builder.add_edge("query_stage", "answer")
    builder.add_edge("answer", END)
    return builder.compile(name=name)


def build_query_stage_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_agent: ValidationAgent | None = None,
    name: str = "query",
) -> CompiledStateGraph:
    """Build the query-stage graph as a first-class LangGraph Studio graph."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_agent=None,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_agent=validation_agent,
    )
    return nodes.query_stage_graph(name=name)
