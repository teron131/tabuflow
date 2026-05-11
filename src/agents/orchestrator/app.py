"""LangGraph Agent Server entrypoint for visible agent graphs."""

from src.agents.orchestrator.graph import build_query_stage_graph
from src.agents.orchestrator.orchestrator import Orchestrator
from src.agents.prep_csv import PrepCsv

orchestrator = Orchestrator()
graph = orchestrator.build_orchestrator_agent()
prep_csv_graph = PrepCsv().graph
query_graph = build_query_stage_graph(llm=orchestrator.llm)
