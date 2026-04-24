"""LangGraph Agent Server entrypoint for visible agent graphs."""

from src.agents.orchestrator.orchestrator import build_orchestrator_graph
from src.agents.prep_agent import PrepAgent
from src.agents.sql_agent import SQLAgent

graph = build_orchestrator_graph()
prep_graph = PrepAgent().graph
sql_graph = SQLAgent().graph

__all__ = [
    "graph",
    "prep_graph",
    "sql_graph",
]
