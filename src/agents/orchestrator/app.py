"""LangGraph Agent Server entrypoint for visible agent graphs."""

from src.agents.orchestrator.graph import build_query_stage_graph
from src.agents.orchestrator.orchestrator import Orchestrator
from src.agents.prep_csv import PrepCsv
from src.agents.prep_pdf import PrepPdf

orchestrator = Orchestrator()
graph = orchestrator.build_orchestrator_agent()
prep_csv_graph = PrepCsv().graph
prep_pdf_graph = PrepPdf().graph
query_graph = build_query_stage_graph(llm=orchestrator.llm)
