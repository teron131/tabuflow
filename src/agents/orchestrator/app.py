"""LangGraph Agent Server entrypoint for visible agent graphs."""

from src.agents.orchestrator.graph import build_query_stage_graph
from src.agents.orchestrator.orchestrator import Orchestrator
from src.agents.prep_stage import PrepStage

orchestrator = Orchestrator()
graph = orchestrator.build_orchestrator_agent()
data_workflow_graph = orchestrator.data_workflow_graph
prep_graph = PrepStage().graph
query_graph = build_query_stage_graph(llm=orchestrator.llm)
