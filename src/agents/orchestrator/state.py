"""State and public schemas for the orchestrator graph."""

from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from .sql_stage import MessageInput, SQLStageContext, SQLStageOutput, SQLStageRuntimeState


@dataclass
class OrchestratorExecutionResult:
    """Normalized result returned by direct and tool-backed orchestrator execution."""

    content: str
    artifact: dict[str, Any]
    agent_artifacts: dict[str, dict[str, Any]]
    active_agent: str | None = None


class OrchestratorInput(MessageInput):
    """Public input schema for the stage-bridged orchestrator graph."""

    max_validation_retries: int = Field(default=2, description="Maximum semantic validation retry count for the SQL stage.")


class OrchestratorOutput(BaseModel):
    """Public output schema for the stage-bridged orchestrator graph."""

    content: str = Field(default="", description="Final assistant-facing response content.")
    artifact: dict[str, Any] = Field(default_factory=dict, description="Compact terminal workflow artifact.")
    agent_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Stage artifacts keyed by stage or worker name.")
    active_agent: str | None = Field(default=None, description="Current or last active stage name.")


class OrchestratorState(
    OrchestratorInput,
    OrchestratorOutput,
    SQLStageContext,
    SQLStageOutput,
    SQLStageRuntimeState,
):
    """Parent graph state shared by the orchestrator-owned workflow stages."""

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list, description="Conversation spine and stage-report log for orchestrator runs.")
    structured_response: Any | None = Field(default=None, description="Structured response produced by a create_agent subgraph.")
    prep_output: dict[str, Any] | None = Field(default=None, description="Serialized prep-stage output artifact.")
    sql_output: dict[str, Any] | None = Field(default=None, description="Serialized SQL-stage output artifact retained across validation retries.")
    validation_attempts: int = Field(default=0, description="Number of semantic validation retry requests made so far.")
