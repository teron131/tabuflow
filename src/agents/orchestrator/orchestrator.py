"""Public orchestrator agent and one-shot execution helpers."""

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.graph.state import CompiledStateGraph
from langsmith import traceable

from ..base import ApplicationAgent
from ..prep_agent import PrepAgent
from ..validation_agent import ValidationAgent
from .graph import build_orchestrator_graph, build_query_stage_graph
from .runtime import OrchestratorRun, SqlLoopResult
from .sql_stage import DraftFn, RuntimeRepairFn
from .state import (
    OrchestratorExecutionResult,
    OrchestratorInput,
    OrchestratorOutput,
    OrchestratorState,
)

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3


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


class Orchestrator(ApplicationAgent):
    """Top-level orchestrator whose graph is the staged data-analysis flow."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
        llm: Any | None = None,
        prep_agent: PrepAgent | None = None,
        sql_drafter: DraftFn | None = None,
        sql_runtime_repairer: RuntimeRepairFn | None = None,
        validation_agent: ValidationAgent | None = None,
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.prep_agent = prep_agent
        self.sql_drafter = sql_drafter
        self.sql_runtime_repairer = sql_runtime_repairer
        self.validation_agent = validation_agent
        self.graph = self.build_graph()
        self.graph_artifacts = self.write_graph_artifacts(
            self.graph,
            filename_stem="orchestrator-graph",
        )

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled orchestrator graph."""
        return build_orchestrator_graph(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_agent=self.prep_agent,
            sql_drafter=self.sql_drafter,
            sql_runtime_repairer=self.sql_runtime_repairer,
            validation_agent=self.validation_agent,
        )

    def invoke(
        self,
        task: str | OrchestratorInput | dict[str, Any],
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run one orchestrator graph invocation."""
        if isinstance(task, OrchestratorInput):
            payload = task
        elif isinstance(task, dict):
            payload = OrchestratorInput.model_validate(task)
        else:
            payload = OrchestratorInput(
                task=task,
                source_files=source_files or [],
                max_validation_retries=max_validation_retries,
            )
        return self.graph.invoke(
            payload,
            config=patch_prep_recursion_limit(config, source_files=payload.source_files),
        )

    def answer(
        self,
        task: str | OrchestratorInput | dict[str, Any],
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> str:
        """Return the final content for one orchestrator graph invocation."""
        result = self.invoke(
            task,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
            config=config,
        )
        output = OrchestratorOutput.model_validate(result)
        return output.content


def trace_orchestrator_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith orchestrator inputs focused on the public request."""
    return {
        "task": inputs.get("task"),
        "source_files": inputs.get("source_files"),
        "prep_recursion_limit": prep_recursion_limit(inputs.get("source_files") or []),
        "max_validation_retries": inputs.get("max_validation_retries"),
        "prompt_provided": bool(str(inputs.get("prompt") or "").strip()),
        "root_dir": None if inputs.get("root_dir") is None else str(inputs["root_dir"]),
    }


def trace_orchestrator_outputs(output: OrchestratorExecutionResult) -> dict[str, Any]:
    """Keep LangSmith orchestrator outputs compact and reviewable."""
    return {
        "content": output.content,
        "artifact": output.artifact,
        "agent_artifacts": output.agent_artifacts,
        "active_agent": output.active_agent,
    }


@traceable(
    name="execute_orchestrator",
    run_type="chain",
    process_inputs=trace_orchestrator_inputs,
    process_outputs=trace_orchestrator_outputs,
)
def execute_orchestrator(
    *,
    task: str,
    source_files: list[str],
    max_validation_retries: int = 2,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_agent: PrepAgent | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_agent: ValidationAgent | None = None,
    config: RunnableConfig | None = None,
) -> OrchestratorExecutionResult:
    """Run the full orchestrator once and return its normalized result."""
    result = Orchestrator(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_agent=prep_agent,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_agent=validation_agent,
    ).invoke(
        task,
        source_files=source_files,
        max_validation_retries=max_validation_retries,
        config=config,
    )
    output = OrchestratorOutput.model_validate(result)
    return OrchestratorExecutionResult(**output.model_dump(mode="python"))


__all__ = [
    "Orchestrator",
    "OrchestratorExecutionResult",
    "OrchestratorInput",
    "OrchestratorOutput",
    "OrchestratorRun",
    "OrchestratorState",
    "SqlLoopResult",
    "build_orchestrator_graph",
    "build_query_stage_graph",
    "execute_orchestrator",
    "prep_recursion_limit",
]
