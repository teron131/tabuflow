"""LangGraph Agent Server entrypoint for visible agent graphs."""

from backend.agents.orchestrator.graph import build_query_stage_graph
from backend.agents.orchestrator.orchestrator import Orchestrator
from backend.agents.prep_csv import PrepCsv
from backend.agents.prep_pdf import PrepPdf

orchestrator = Orchestrator()
graph = orchestrator.build_orchestrator_agent()
prep_csv_graph = PrepCsv().graph
prep_pdf_graph = PrepPdf().graph
query_graph = build_query_stage_graph(llm=orchestrator.llm)
