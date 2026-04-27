"""Public orchestrator agent and one-shot execution helpers."""

from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langsmith import traceable

from ..base import ApplicationAgent
from ..prep_stage import PrepStage
from ..validation_stage import ValidationStage
from .graph import build_data_workflow_graph
from ..query_stage import DraftFn, RuntimeRepairFn
from .stage_tools import make_orchestrator_stages
from .state import (
    OrchestratorInput,
    OrchestratorOutput,
)

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3
ORCHESTRATOR_SYSTEM_PROMPT = """You are the user-facing data assistant.

Answer normal conversational messages directly.
When the user wants to inspect, prepare, analyze, query, compute, compare, or summarize source data, use the stage tools.
Use prep_stage before query_stage when source files need preparation.
Use query_stage with the compact state returned by prep_stage when querying already prepared data.
Do not invent saved view names, SQL paths, row counts, or artifact details; use tool results for those facts.
"""


def prep_recursion_limit(source_files: list[str]) -> int:
    """Return the prep-stage recursion budget for declared and runtime-discovered files."""
    return max(
        MIN_PREP_RECURSION_LIMIT,
        PREP_RECURSION_LIMIT_PER_SOURCE_FILE * len(source_files),
    )


def patch_prep_recursion_limit(
    config: RunnableConfig | None,
    *,
    source_files: list[str],
) -> RunnableConfig:
    """Ensure graph invocations have enough room for the prep ReAct loop."""
    required_limit = prep_recursion_limit(source_files)
    configured_limit = None if config is None else config.get("recursion_limit")
    if isinstance(configured_limit, int):
        required_limit = max(required_limit, configured_limit)
    return patch_config(config, recursion_limit=required_limit)


OrchestratorRequest = str | OrchestratorInput | dict[str, Any]


def build_orchestrator_input(
    message: OrchestratorRequest | None,
    *,
    source_files: list[str] | None = None,
    max_validation_retries: int = 2,
) -> OrchestratorInput:
    """Normalize chat-facing input into the orchestrator state schema."""
    if isinstance(message, OrchestratorInput):
        return message
    if isinstance(message, dict):
        payload = dict(message)
        if source_files is not None:
            payload.setdefault("source_files", source_files)
        else:
            payload.setdefault("source_files", [])
        payload.setdefault("max_validation_retries", max_validation_retries)
        return OrchestratorInput.model_validate(payload)

    if message is None:
        raise ValueError("Orchestrator.invoke() requires a message.")
    return OrchestratorInput(
        message=message,
        source_files=source_files or [],
        max_validation_retries=max_validation_retries,
    )


class Orchestrator(ApplicationAgent):
    """Composition root for the user-facing orchestrator and data workflow."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
        llm: Any | None = None,
        prep_stage: PrepStage | None = None,
        sql_drafter: DraftFn | None = None,
        sql_runtime_repairer: RuntimeRepairFn | None = None,
        validation_stage: ValidationStage | None = None,
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.prep_stage = prep_stage
        self.sql_drafter = sql_drafter
        self.sql_runtime_repairer = sql_runtime_repairer
        self.validation_stage = validation_stage
        self.data_workflow_graph = self.build_data_workflow_graph()
        self.graph = self.data_workflow_graph
        self.graph_artifacts = self.write_graph_artifacts(
            self.data_workflow_graph,
            filename_stem="data-workflow-graph",
        )

    def build_data_workflow_graph(self) -> CompiledStateGraph:
        """Build the compiled data workflow graph."""
        return build_data_workflow_graph(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_stage=self.prep_stage,
            sql_drafter=self.sql_drafter,
            sql_runtime_repairer=self.sql_runtime_repairer,
            validation_stage=self.validation_stage,
        )

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled data workflow graph."""
        return self.build_data_workflow_graph()

    def build_stages(self) -> list[BaseTool]:
        """Build callable stage handles around the current stage subgraphs."""
        return make_orchestrator_stages(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_stage=self.prep_stage,
            sql_drafter=self.sql_drafter,
            sql_runtime_repairer=self.sql_runtime_repairer,
            validation_stage=self.validation_stage,
        )

    def build_orchestrator_agent(self) -> CompiledStateGraph:
        """Build the user-facing orchestrator agent that can call stage tools."""
        return create_agent(
            model=self.llm,
            tools=self.build_stages(),
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            name="orchestrator",
        )

    def invoke(
        self,
        message: OrchestratorRequest | None = None,
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run one data workflow invocation."""
        payload = build_orchestrator_input(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
        )
        return self.data_workflow_graph.invoke(
            payload,
            config=patch_prep_recursion_limit(config, source_files=payload.source_files),
        )

    def answer(
        self,
        message: OrchestratorRequest | None = None,
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> str:
        """Return the final content for one data workflow invocation."""
        result = self.invoke(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
            config=config,
        )
        output = OrchestratorOutput.model_validate(result)
        return output.content


def trace_data_workflow_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith data workflow inputs focused on the public request."""
    return {
        "message": inputs.get("message"),
        "source_files": inputs.get("source_files"),
        "prep_recursion_limit": prep_recursion_limit(inputs.get("source_files") or []),
        "max_validation_retries": inputs.get("max_validation_retries"),
        "prompt_provided": bool(str(inputs.get("prompt") or "").strip()),
        "root_dir": None if inputs.get("root_dir") is None else str(inputs["root_dir"]),
    }


def trace_data_workflow_outputs(output: OrchestratorOutput) -> dict[str, Any]:
    """Keep LangSmith data workflow outputs compact and reviewable."""
    return {
        "content": output.content,
        "artifact": output.artifact,
        "stage_artifacts": output.stage_artifacts,
    }


@traceable(
    name="execute_data_workflow",
    run_type="chain",
    process_inputs=trace_data_workflow_inputs,
    process_outputs=trace_data_workflow_outputs,
)
def execute_data_workflow(
    *,
    message: str | None = None,
    source_files: list[str] | None = None,
    max_validation_retries: int = 2,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_stage: PrepStage | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_stage: ValidationStage | None = None,
    config: RunnableConfig | None = None,
) -> OrchestratorOutput:
    """Run the fixed data workflow once and return its normalized result."""
    result = Orchestrator(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_stage=prep_stage,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_stage=validation_stage,
    ).invoke(
        message,
        source_files=source_files,
        max_validation_retries=max_validation_retries,
        config=config,
    )
    return OrchestratorOutput.model_validate(result)
