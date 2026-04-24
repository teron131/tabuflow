"""State and public schemas for the orchestrator graph."""

from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from ..sql_agent import SQLAgentOutput, SQLPlan
from ..validation_agent import ValidationOutput
from .payloads import build_result_artifact, build_result_message
from .skill_context import WorkerSkillPayload


@dataclass
class OrchestratorRun:
    """Shared orchestrator run state used across prep, SQL, and save stages."""

    task: str
    source_files: list[str]
    skill_payload: WorkerSkillPayload
    run_id: str = field(default_factory=lambda: uuid4().hex[:8])
    trace: list[str] = field(default_factory=list)
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = field(default_factory=list)

    def append_trace(self, *messages: str) -> None:
        """Append one or more trace messages while keeping the trace compact."""
        for message in messages:
            self.trace = [*self.trace, message][-12:]

    def result(
        self,
        *,
        status: str,
        outcome: str,
        completion_reason: str | None,
        selected_targets: list[str],
        candidate_sql: str | None,
        sql_result: dict[str, Any] | None,
        saved_view_name: str | None,
        saved_view: dict[str, Any] | None,
        last_error: str | None,
        validation_feedback: dict[str, Any] | None,
        validation_attempts: int,
        trace: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the caller-facing result from the current run state."""
        artifact = build_result_artifact(
            task=self.task,
            status=status,
            outcome=outcome,
            completion_reason=completion_reason,
            source_files=self.source_files,
            database_path=self.database_path,
            extracted_targets=self.extracted_targets,
            selected_targets=selected_targets,
            candidate_sql=candidate_sql,
            sql_result=sql_result,
            saved_view_name=saved_view_name,
            saved_view=saved_view,
            last_error=last_error,
            validation_feedback=validation_feedback,
            validation_attempts=validation_attempts,
            trace=self.trace if trace is None else trace,
        )
        return build_result_message(artifact), artifact


@dataclass
class SqlLoopResult:
    """State accumulated across orchestrator-owned SQL attempts."""

    output: SQLAgentOutput | None = None
    validation_output: ValidationOutput | None = None
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0
    validated: bool = False


@dataclass
class OrchestratorExecutionResult:
    """Normalized result returned by direct and tool-backed orchestrator execution."""

    content: str
    artifact: dict[str, Any]
    agent_artifacts: dict[str, dict[str, Any]]
    active_agent: str | None


class OrchestratorInput(BaseModel):
    """Public input schema for the stage-bridged orchestrator graph."""

    task: str
    source_files: list[str]
    max_prep_trials: int = 2
    max_validation_retries: int = 2


class OrchestratorOutput(BaseModel):
    """Public output schema for the stage-bridged orchestrator graph."""

    content: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    agent_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    active_agent: str | None = None


class OrchestratorState(OrchestratorInput, OrchestratorOutput):
    """Parent graph state that bridges the worker subgraph stages."""

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    structured_response: Any | None = None
    run_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    prep_output: dict[str, Any] | None = None
    sql_output: dict[str, Any] | None = None
    validation_output: dict[str, Any] | None = None
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0
    validated: bool = False
    question: str = ""
    preferred_targets: list[str] = Field(default_factory=list)
    worker_context: str = ""
    max_suggestions: int = 3
    max_repairs: int = 2
    sample_rows: int = 3
    text_value_hints: int = 3
    status: str = "pending"
    selected_targets: list[str] = Field(default_factory=list)
    candidate_sql: str | None = None
    repair_hints: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    attempts: int = 0
    rationale: str | None = None
    last_error: str | None = None
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    inspected_targets: list[dict[str, Any]] = Field(default_factory=list)
    plan: SQLPlan | None = None
    repair_count: int = 0
