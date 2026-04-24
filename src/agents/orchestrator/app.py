"""LangGraph Agent Server entrypoint for the orchestrator graph."""

from src.agents.orchestrator.workflow import build_workflow_graph
from src.agents.orchestrator.orchestrator import Orchestrator
from src.agents.prep_agent import PrepAgent
from src.agents.sql_agent import SQLAgent
from src.agents.validation_agent import ValidationAgent

graph = Orchestrator().graph
workflow_graph = build_workflow_graph()
prep_graph = PrepAgent().graph
sql_graph = SQLAgent().graph
validation_graph = ValidationAgent().graph
