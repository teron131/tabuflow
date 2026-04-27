"""LangGraph wiring for the staged data-analysis orchestrator."""

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..prep_stage import PrepStage
from ..validation_stage import ValidationStage
from .nodes import OrchestratorNodes, route_after_prep_stage
from ..query_stage import DraftFn, RuntimeRepairFn
from .state import OrchestratorInput, OrchestratorOutput, OrchestratorState


def build_data_workflow_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_stage: PrepStage | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_stage: ValidationStage | None = None,
    name: str = "data_workflow",
) -> CompiledStateGraph:
    """Build the fixed data workflow graph that owns the prep and query stages."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_stage=prep_stage,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_stage=validation_stage,
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
    validation_stage: ValidationStage | None = None,
    name: str = "query",
) -> CompiledStateGraph:
    """Build the query-stage graph as a first-class LangGraph Studio graph."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_stage=None,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_stage=validation_stage,
    )
    return nodes.query_stage_graph(name=name)
