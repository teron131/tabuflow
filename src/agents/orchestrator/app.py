"""LangGraph Agent Server entrypoint for visible agent graphs."""

from src.agents.orchestrator.graph import build_query_stage_graph
from src.agents.orchestrator.orchestrator import Orchestrator
from src.agents.prep_agent import PrepAgent

orchestrator = Orchestrator()
graph = orchestrator.graph
prep_graph = PrepAgent().graph
query_graph = build_query_stage_graph(llm=orchestrator.llm)

__all__ = [
    "graph",
    "orchestrator",
    "prep_graph",
    "query_graph",
]
