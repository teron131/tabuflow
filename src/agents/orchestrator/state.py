"""State and public schemas for the orchestrator graph."""

from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from .sql_stage import SQLStageContext, SQLStageOutput, SQLStageRuntimeState, TaskInput


@dataclass
class OrchestratorExecutionResult:
    """Normalized result returned by direct and tool-backed orchestrator execution."""

    content: str
    artifact: dict[str, Any]
    agent_artifacts: dict[str, dict[str, Any]]
    active_agent: str | None = None


class OrchestratorInput(TaskInput):
    """Public input schema for the stage-bridged orchestrator graph."""

    source_files: list[str]
    max_validation_retries: int = 2


class OrchestratorOutput(BaseModel):
    """Public output schema for the stage-bridged orchestrator graph."""

    content: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    agent_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    active_agent: str | None = None


class OrchestratorState(
    OrchestratorInput,
    OrchestratorOutput,
    SQLStageContext,
    SQLStageOutput,
    SQLStageRuntimeState,
):
    """Parent graph state shared by the orchestrator-owned workflow stages."""

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    structured_response: Any | None = None
    worker_instructions: str = ""
    prep_output: dict[str, Any] | None = None
    sql_output: dict[str, Any] | None = None
    validation_attempts: int = 0
