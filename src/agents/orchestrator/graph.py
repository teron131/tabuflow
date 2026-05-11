"""LangGraph wiring for standalone orchestrator stage graphs."""

from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..validation_stage import ValidationStage
from ..query_stage import SQLRepairerFn, SQLWriterFn
from .nodes import OrchestratorNodes


def build_query_stage_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    sql_writer: SQLWriterFn | None = None,
    sql_repairer: SQLRepairerFn | None = None,
    validation_stage: ValidationStage | None = None,
    name: str = "query",
) -> CompiledStateGraph:
    """Build the query-stage graph as a first-class LangGraph Studio graph."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_csv=None,
        prep_pdf=None,
        sql_writer=sql_writer,
        sql_repairer=sql_repairer,
        validation_stage=validation_stage,
    )
    return nodes.query_stage_graph(name=name)
